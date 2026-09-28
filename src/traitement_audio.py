"""Traitement des messages des invités après enregistrement.

Chaîne validée au banc du 22 au 25/09/2026 (docs/banc_audio/JOURNAL.md), sur
le micro électret du jack MIC :

1. **declip** : les zones saturées (|signal| >= 95 %) — charge du bias au
   démarrage, parasites d'alimentation — sont inexploitables électriquement ;
   on les remplace par une interpolation linéaire ;
2. **despike** : les clics électriques courts (<= 5 ms, saut > 0,05 entre deux
   échantillons) sont interpolés de la même façon ;
3. **passe-haut 80 Hz + coupe-bandes 50/100/150 Hz** (Q 25, -45 dB) contre le
   hum secteur, capté sur la ligne micro en amont du codec ;
4. **sox noisered** (0,25), avec un profil pris sur la fenêtre de 1,5 s la
   plus calme et sans clic ;
5. **expandeur doux** (mcompand) : abaisse les pauses pour effacer le
   « scintillement » que laisse noisered, sans toucher la voix ;
6. **normalisation** : le niveau de la voix est ramené à -20 dBFS (gain
   plafonné, limiteur contre les claquements). Ajoutée après le banc : sans
   elle, la voix débruitée sortait vers -46 dBFS, trop faible à la réécoute.

Rejetés au banc : RNNoise (détruit les transitoires), afftdn (inefficace sur
le 50 Hz), peigne de coupe-bandes harmoniques et passe-bas 4 kHz (perte de
voix pour rien).

Le brut est d'abord copié dans `messages/brut/`, puis le fichier traité
remplace atomiquement `messages/<nom>.wav` : restitution, transcription et
synchronisation Drive voient le message traité sans rien changer, et
`messages/` contient à tout instant un fichier valide. Un retraitement repart
toujours du brut. En cas d'échec (sox absent, fichier illisible), le brut
reste en place et l'erreur est journalisée — rien n'est jamais perdu.

Lancé en arrière-plan par livre_dor.py à la fin de chaque enregistrement.

Usage :
    python3 src/traitement_audio.py messages/message_….wav   # un ou plusieurs fichiers
    python3 src/traitement_audio.py --en-attente             # messages sans copie dans brut/
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import config
import fichiers

try:
    import numpy as np
    _NUMPY_ERREUR = None
except ImportError as exc:  # poste de développement sans numpy : traiter() échoue proprement
    np = None
    _NUMPY_ERREUR = exc  # distingue « non installé » de « bibliothèque système manquante »

logger = logging.getLogger(__name__)

BRUT_DIRNAME = "brut"

# Constantes du banc (pipeline_v2.py), exprimées en temps pour ne pas dépendre
# de la cadence : 80 échantillons à 16 kHz = 5 ms, blocs de 320 = 20 ms.
SEUIL_CLIC = 0.05            # saut entre deux échantillons (-26 dBFS)
CLIC_FUSION_MS = 5.0         # deux sauts plus proches appartiennent au même clic
CLIC_DUREE_MAX_MS = 5.0      # au-delà, c'est de la parole
CLIC_MARGE_MS = 1.25         # marge interpolée autour d'un clic (20 éch. à 16 kHz)
SEUIL_SATURATION = 0.95
SATURATION_MARGE_MS = 5.0
BLOC_MS = 20.0
PROFIL_DUREE_SEC = 1.5
# Écart minimal entre la parole (95e centile des blocs) et la fenêtre de
# profil : en deçà, la fenêtre « la plus calme » contient de la voix, et
# noisered la retirerait avec le bruit. Volontairement bas : sur le jack MIC
# la voix ne dépasse le bruit brut que de 7 à 9 dB (banc du 25/09, premiers
# messages du 28/09), et c'est précisément là que noisered est indispensable.
PROFIL_ECART_MIN_DB = 6.0

EXPANDEUR = "0.001,0.12 -82,-105,-60,-60,-40,-40,-20,-20"
NOTCH_FREQUENCES = (50, 100, 150)


class ErreurTraitement(Exception):
    pass


def brut_path(path: Path) -> Path:
    """Emplacement de la copie brute d'un message : messages/brut/<nom>.wav."""
    return path.parent / BRUT_DIRNAME / path.name


# --- Signal ------------------------------------------------------------------


def _echantillons(ms: float, sr: int) -> int:
    return max(1, int(round(ms * sr / 1000)))


def charger(path: Path) -> Tuple["np.ndarray", int]:
    """Canal gauche en flottants [-1, 1] et cadence ; les deux pistes sont identiques."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ErreurTraitement(f"format non géré ({8 * w.getsampwidth()} bits)")
        canaux = w.getnchannels()
        sr = w.getframerate()
        brut = w.readframes(w.getnframes())
    d = np.frombuffer(brut, dtype=np.int16)
    d = d[: len(d) - len(d) % canaux].reshape(-1, canaux)[:, 0]
    return d.astype(np.float64) / 32768, sr


def ecrire(path: Path, d: "np.ndarray", sr: int, canaux: int = 2) -> None:
    """WAV 16 bits ; stéréo L = R par défaut, comme l'enregistrement d'origine."""
    # Arrondi et échelle 32768, inverse exacte de charger() : une troncature
    # effacerait les échantillons à ±1 LSB (le silence après débruitage).
    pcm = np.clip(np.round(d * 32768), -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(canaux)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm, canaux).tobytes())


def _plages(indices: "np.ndarray", ecart_max: int) -> List[Tuple[int, int]]:
    """Regroupe des indices croissants en plages [début, fin] séparées de plus de ecart_max."""
    plages: List[Tuple[int, int]] = []
    for i in indices:
        if plages and i - plages[-1][1] <= ecart_max:
            plages[-1] = (plages[-1][0], int(i))
        else:
            plages.append((int(i), int(i)))
    return plages


def _interpoler(d: "np.ndarray", a: int, b: int) -> None:
    if b > a:
        d[a:b] = np.linspace(d[a], d[b - 1], b - a)


def declip(d: "np.ndarray", sr: int) -> int:
    """Interpole les zones saturées ; retourne leur nombre."""
    marge = _echantillons(SATURATION_MARGE_MS, sr)
    plages = _plages(np.where(np.abs(d) >= SEUIL_SATURATION)[0], 1)
    for a, b in plages:
        _interpoler(d, max(0, a - marge), min(len(d), b + marge))
    return len(plages)


def clics(d: "np.ndarray", sr: int, debut: int = 0) -> List[Tuple[int, int]]:
    """Clics courts : sauts brusques groupés en événements de moins de 5 ms.

    Les événements plus longs sont de la parole ; ceux qui commencent avant
    `debut` tombent dans la rampe du bias, laissée au declip.
    """
    plages = _plages(np.where(np.abs(np.diff(d)) > SEUIL_CLIC)[0],
                     _echantillons(CLIC_FUSION_MS, sr) - 1)
    duree_max = _echantillons(CLIC_DUREE_MAX_MS, sr)
    return [(a, b) for a, b in plages if b - a <= duree_max and a >= debut]


def despike(d: "np.ndarray", evenements: List[Tuple[int, int]], sr: int) -> None:
    marge = _echantillons(CLIC_MARGE_MS, sr)
    for a, b in evenements:
        _interpoler(d, max(0, a - marge), min(len(d), b + marge))


def niveaux_blocs(d: "np.ndarray", taille: int) -> "np.ndarray":
    """Niveau RMS en dBFS de chaque bloc complet."""
    n = len(d) // taille
    if n == 0:
        return np.array([])
    blocs = d[: n * taille].reshape(n, taille)
    return 20 * np.log10(np.sqrt((blocs ** 2).mean(axis=1)) + 1e-12)


def fenetre_profil(d: "np.ndarray", evenements: List[Tuple[int, int]], sr: int,
                   debut_sec: float) -> Optional[float]:
    """Début (s) de la fenêtre de 1,5 s la plus calme après debut_sec.

    Une fenêtre sans clic est préférée ; s'il n'y en a aucune (micro qui
    craque souvent), la plus calme est retenue quand même — le signal reçu
    est déjà despiké. None si le message est trop court, ou si même la
    fenêtre la plus calme n'est pas nettement sous le niveau de parole
    (message parlé d'un bout à l'autre) : mieux vaut alors ne pas débruiter
    que retirer de la voix.
    """
    taille = _echantillons(BLOC_MS, sr)
    db = niveaux_blocs(d, taille)
    largeur = int(round(PROFIL_DUREE_SEC * 1000 / BLOC_MS))
    premier = int(round(debut_sec * 1000 / BLOC_MS))
    if len(db) - premier < largeur:
        return None

    avec_clic = np.zeros(len(db), dtype=bool)
    for a, b in evenements:
        avec_clic[max(0, a // taille - 1): min(len(db), b // taille + 2)] = True

    meilleur = meilleur_avec_clic = None
    for i in range(premier, len(db) - largeur + 1):
        m = db[i:i + largeur].mean()
        if not avec_clic[i:i + largeur].any():
            if meilleur is None or m < meilleur[0]:
                meilleur = (m, i)
        elif meilleur_avec_clic is None or m < meilleur_avec_clic[0]:
            meilleur_avec_clic = (m, i)
    meilleur = meilleur or meilleur_avec_clic
    if meilleur is None:
        return None
    if np.percentile(db[premier:], 95) - meilleur[0] < PROFIL_ECART_MIN_DB:
        return None
    return meilleur[1] * BLOC_MS / 1000


def gain_normalisation(d: "np.ndarray", sr: int) -> Optional[float]:
    """Gain (dB) qui amène la voix à TRAITEMENT_NIVEAU_VOIX_DBFS, plafonné.

    Niveau de la voix = 95e centile des blocs de 20 ms : robuste aux
    claquements brefs, et les pauses (abaissées par l'expandeur) ne comptent
    pas. None si le signal est vide.
    """
    db = niveaux_blocs(d, _echantillons(BLOC_MS, sr))
    if len(db) == 0:
        return None
    gain = config.TRAITEMENT_NIVEAU_VOIX_DBFS - float(np.percentile(db, 95))
    return min(gain, config.TRAITEMENT_GAIN_MAX_DB)


# --- sox ---------------------------------------------------------------------


def _sox(*args: str) -> None:
    # -R : dither reproductible. Sans lui, sox tire un dither différent à
    # chaque appel et un retraitement ne redonnerait pas le même fichier.
    result = subprocess.run(["sox", "-R", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ErreurTraitement(f"sox {args[2] if len(args) > 2 else ''} : "
                               f"{(result.stderr or '').strip()}")


def _chaine(source: Path, sortie: Path, tmp: Path) -> str:
    """Applique la chaîne complète de source vers sortie ; retourne un résumé pour le journal."""
    d, sr = charger(source)
    debut = int(config.TRAITEMENT_PROFIL_DEBUT_SEC * sr)

    n_sat = declip(d, sr)
    evenements = clics(d, sr, debut)
    despike(d, evenements, sr)
    # sox travaille en mono (les deux pistes sont identiques) : traiter les
    # deux canaux séparément les ferait diverger par le dither. Silence
    # ajouté en fin, retiré à la fin : noisered ampute la fin du signal d'une
    # demi-fenêtre (1024 échantillons, 64 ms à 16 kHz) — le banc perdait ainsi
    # la fin de chaque message.
    f_des = tmp / "despike.wav"
    ecrire(f_des, np.concatenate([d, np.zeros(sr // 4)]), sr, canaux=1)

    f_courant = tmp / "notch.wav"
    args = [str(f_des), str(f_courant), "highpass", "80"]
    if config.TRAITEMENT_NOTCH:
        for f in NOTCH_FREQUENCES:
            args += ["equalizer", str(f), "25q", "-45"]
    _sox(*args)

    t0 = fenetre_profil(d, evenements, sr, config.TRAITEMENT_PROFIL_DEBUT_SEC)
    if t0 is None:
        profil_txt = "noisered sauté (pas de silence exploitable)"
    elif config.TRAITEMENT_NR > 0:
        # Profil pris sur le signal despiké, avant les coupe-bandes : c'est la
        # combinaison validée à l'écoute au banc.
        prof = tmp / "profil"
        _sox(str(f_des), "-n", "trim", f"{t0:.2f}", f"{PROFIL_DUREE_SEC}", "noiseprof", str(prof))
        f_nr = tmp / "noisered.wav"
        _sox(str(f_courant), str(f_nr), "noisered", str(prof), f"{config.TRAITEMENT_NR:.2f}")
        f_courant = f_nr
        profil_txt = f"noisered {config.TRAITEMENT_NR:.2f}, profil {t0:.2f}-{t0 + PROFIL_DUREE_SEC:.2f} s"
    else:
        profil_txt = "noisered désactivé"

    if config.TRAITEMENT_EXPANDEUR:
        f_exp = tmp / "expandeur.wav"
        _sox(str(f_courant), str(f_exp), "mcompand", EXPANDEUR)
        f_courant = f_exp

    norm_txt = ""
    if config.TRAITEMENT_NORMALISATION:
        x, _ = charger(f_courant)
        gain = gain_normalisation(x[debut:len(d)], sr)
        if gain is not None:
            f_norm = tmp / "normalisation.wav"
            # -l : limiteur de sox, les crêtes (claquement du raccroché)
            # sont écrasées au lieu d'écrêter.
            _sox(str(f_courant), str(f_norm), "gain", "-l", f"{gain:.1f}")
            f_courant = f_norm
            norm_txt = f", gain {gain:+.1f} dB"

    resultat, _ = charger(f_courant)
    resultat = np.concatenate([resultat, np.zeros(max(0, len(d) - len(resultat)))])[:len(d)]
    ecrire(sortie, resultat, sr)
    return (f"{n_sat} zone(s) saturée(s), {len(evenements)} clic(s), {profil_txt}{norm_txt}"
            f"{'' if config.TRAITEMENT_NOTCH else ', sans coupe-bandes'}"
            f"{'' if config.TRAITEMENT_EXPANDEUR else ', sans expandeur'}")


def traiter(path: Path) -> bool:
    """Traite un message en place, brut conservé dans brut/ ; jamais d'exception."""
    path = Path(path)
    if np is None:
        logger.error(
            "Traitement de %s impossible : numpy non importable (%s, python %s).",
            path.name, _NUMPY_ERREUR, sys.executable,
        )
        return False
    if shutil.which("sox") is None:
        logger.error("Traitement de %s impossible : sox absent (apt install sox).", path.name)
        return False

    brut = brut_path(path)
    debut = time.monotonic()
    brut_cree = False
    try:
        if not brut.exists():
            if not path.exists():
                logger.error("Traitement : %s introuvable.", path)
                return False
            brut.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, brut)
            brut_cree = True
        # Écrit à côté du message puis renommé : os.replace est atomique sur
        # un même système de fichiers. Suffixe .tmp, et non .wav, pour que ni
        # la restitution ni rclone ne voient le fichier intermédiaire.
        partiel = path.with_name(path.name + ".tmp")
        with tempfile.TemporaryDirectory(prefix="traitement_") as tmp:
            resume = _chaine(brut, partiel, Path(tmp))
        os.replace(partiel, path)
    except (ErreurTraitement, OSError, wave.Error, EOFError, ValueError) as exc:
        logger.error("Traitement de %s en échec, brut conservé : %s", path.name, exc)
        # Le message est resté brut : sans sa copie dans brut/, il reste listé
        # par --en-attente et pourra être retraité.
        for reste in [path.with_name(path.name + ".tmp")] + ([brut] if brut_cree else []):
            try:
                reste.unlink()
            except OSError:
                pass
        return False

    logger.info("Message traité : %s (%s) en %.1f s", path.name, resume,
                time.monotonic() - debut)
    return True


def messages_en_attente(dossier: Optional[Path] = None) -> List[Path]:
    """Messages de messages/ sans copie brute : jamais traités, ou traitement interrompu."""
    dossier = dossier or config.MESSAGES_DIR
    return sorted(p for p in fichiers.fichiers_wav(dossier) if not brut_path(p).exists())


def lancer_en_arriere_plan(path: Path) -> Optional[subprocess.Popen]:
    """Lance le traitement d'un message dans un processus séparé, en priorité basse.

    Ne bloque pas : la machine à états retourne aussitôt en attente et un
    invité suivant peut décrocher. `nice` laisse la priorité à l'arecord
    suivant ; le processus a sa propre session et survit à un redémarrage
    du service. Sa sortie (lignes de journal) est retransmise au journal de
    l'appelant par un fil dédié.
    """
    cmd = [sys.executable, str(Path(__file__).resolve()), str(path)]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "19", *cmd]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
    except OSError as exc:
        logger.error("Impossible de lancer le traitement de %s : %s", Path(path).name, exc)
        return None

    def relayer() -> None:
        for ligne in proc.stdout or ():
            ligne = ligne.rstrip()
            if not ligne:
                continue
            niveau, _, texte = ligne.partition(" ")
            logger.log(getattr(logging, niveau, logging.INFO), "%s", texte or ligne)
        proc.wait()

    threading.Thread(target=relayer, name="traitement-audio", daemon=True).start()
    return proc


def _cli() -> None:
    # Format sans horodatage, niveau en tête : lancer_en_arriere_plan() relit
    # ces lignes et en retire le niveau pour les rejouer dans son journal.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Traitement des messages enregistrés.")
    parser.add_argument("fichiers", nargs="*", type=Path, help="messages à (re)traiter")
    parser.add_argument("--en-attente", action="store_true",
                        help="traite les messages de messages/ sans copie dans brut/")
    args = parser.parse_args()

    a_traiter = list(args.fichiers)
    if args.en_attente:
        a_traiter += messages_en_attente()
    if not a_traiter:
        parser.error("aucun fichier à traiter")
    echecs = sum(not traiter(f) for f in a_traiter)
    sys.exit(1 if echecs else 0)


if __name__ == "__main__":
    _cli()
