"""Traitement des messages des invités après enregistrement.

Chaîne volontairement légère (02/10/2026) : la chaîne du banc (declip,
despike, coupe-bandes Q 25 à -45 dB, sox noisered, expandeur) rendait une
voix faussée et métallique. Mesuré sur les prises du 02/10, téléphone monté
(silence et messages réels) : 94 % de l'énergie du bruit de fond est du
ronflement secteur, 50 Hz et ses harmoniques jusqu'à 4 kHz ; le souffle
restant est faible. Il suffit donc de :

1. **anti-ronflement** : le ronflement est modélisé comme une somme
   d'harmoniques de la fréquence secteur, suivie trame par trame (elle
   dérive de ± 0,05 Hz autour de 50 Hz), puis soustrait. Chaque harmonique
   n'est retirée que sur ~1 Hz de large : la voix entre les raies n'est pas
   touchée, contrairement à un peigne de coupe-bandes ;
2. **débruitage léger** : atténuation spectrale (Wiener, décision dirigée)
   plafonnée à TRAITEMENT_DEBRUITAGE_DB, sur un bruit estimé dans les
   passages les plus calmes. Le plafond et le lissage évitent le « bruit
   musical » et le timbre métallique de noisered ;
3. **normalisation** : le niveau de la voix est ramené à -20 dBFS (gain
   plafonné, limiteur contre les claquements). Un simple gain : le timbre
   n'est pas modifié.

Plus de declip ni de despike : sur les prises réelles, les « saturations » et
les « clics » détectés étaient surtout la voix elle-même (micro proche, fort
niveau), et leur interpolation la déformait.

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

BLOC_MS = 20.0

# Anti-ronflement. Trames de 1 s à 50 % de recouvrement : assez longues pour
# que chaque raie ne retire que ~1 Hz de spectre, assez courtes pour suivre
# la dérive du secteur et les variations d'amplitude du ronflement.
SECTEUR_HZ = 50.0
SECTEUR_ECART_MAX_HZ = 0.15     # recherche de la fréquence secteur à ± 0,15 Hz
SECTEUR_PAS_HZ = 0.002
RONFLEMENT_TRAME_SEC = 1.0
RONFLEMENT_FMAX_HZ = 4000.0     # au-delà, plus aucune raie au-dessus du souffle
RONFLEMENT_LISSAGE = 5          # médiane glissante de la fréquence, en trames

# Débruitage : STFT 32 ms / pas 8 ms à 16 kHz.
STFT_MS = 32.0
STFT_PAS_MS = 8.0
BRUIT_PART_CALME = 0.2          # bruit = moyenne des 20 % de trames les plus calmes
DD_ALPHA = 0.98                 # lissage « décision dirigée » (anti bruit musical)
# Écart minimal entre la parole (95e centile des trames) et les trames
# calmes : en deçà, le message est parlé d'un bout à l'autre, le « bruit »
# estimé contiendrait de la voix, et le débruitage est sauté.
BRUIT_ECART_MIN_DB = 6.0


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


def niveaux_blocs(d: "np.ndarray", taille: int) -> "np.ndarray":
    """Niveau RMS en dBFS de chaque bloc complet."""
    n = len(d) // taille
    if n == 0:
        return np.array([])
    blocs = d[: n * taille].reshape(n, taille)
    return 20 * np.log10(np.sqrt((blocs ** 2).mean(axis=1)) + 1e-12)


def _hann(n: int) -> "np.ndarray":
    """Fenêtre de Hann périodique : à 50 % de recouvrement, sa somme vaut 1."""
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)


# --- Anti-ronflement ---------------------------------------------------------


def frequence_secteur(trame: "np.ndarray", sr: int) -> float:
    """Fréquence secteur (Hz) d'une trame, par maximum d'énergie des raies 1 et 3.

    Les raies 50 et 150 Hz sont les plus fortes du ronflement et la voix y
    est faible. Chaque raie est d'abord démodulée autour de sa valeur
    nominale puis moyennée par paquets de 10 ms (l'écart cherché, < 0,5 Hz,
    n'y tourne que de quelques centièmes de radian) : la recherche fine ne
    porte alors que sur ~100 points par trame, ce qui la rend négligeable
    même sur le Pi Zero.
    """
    n = len(trame)
    t = np.arange(n) / sr
    x = trame * _hann(n)
    paquet = _echantillons(10.0, sr)
    m = n // paquet
    t_paquets = (np.arange(m) * paquet + (paquet - 1) / 2) / sr
    grille = SECTEUR_HZ + np.arange(-SECTEUR_ECART_MAX_HZ,
                                    SECTEUR_ECART_MAX_HZ + SECTEUR_PAS_HZ / 2, SECTEUR_PAS_HZ)
    energie = np.zeros(len(grille))
    for k in (1, 3):
        y = x * np.exp(-2j * np.pi * k * SECTEUR_HZ * t)
        y = y[: m * paquet].reshape(m, paquet).sum(axis=1)
        rot = np.exp(-2j * np.pi * k * np.outer(grille - SECTEUR_HZ, t_paquets))
        energie += np.abs(rot @ y) ** 2
    return float(grille[int(np.argmax(energie))])


def anti_ronflement(d: "np.ndarray", sr: int) -> Tuple["np.ndarray", float]:
    """Retire le ronflement secteur ; retourne le signal et la fréquence médiane.

    Sur chaque trame de 1 s, l'amplitude et la phase de chaque harmonique
    (et la composante continue) sont mesurées à la fréquence secteur de la
    trame, puis le modèle est retranché par recouvrement-addition (Hann à
    50 %). Les harmoniques d'une même trame étant orthogonales, une simple
    projection suffit : pas de moindres carrés.
    """
    taille = int(RONFLEMENT_TRAME_SEC * sr)
    pas = taille // 2
    n_trames = max(1, -(-(len(d) - pas) // pas)) + 1
    x = np.concatenate([np.zeros(pas), d, np.zeros(n_trames * pas + taille - len(d) - pas)])
    trames = [x[i * pas:i * pas + taille] for i in range(n_trames)]

    f0 = np.array([frequence_secteur(tr, sr) for tr in trames])
    demi = RONFLEMENT_LISSAGE // 2
    f0 = np.array([np.median(f0[max(0, i - demi):i + demi + 1]) for i in range(len(f0))])

    fen = _hann(taille)
    poids = fen / fen.sum()
    t = np.arange(taille) / sr
    ronflement = np.zeros(len(x))
    for i, (tr, f) in enumerate(zip(trames, f0)):
        tw = tr * poids
        modele = np.full(taille, tw.sum())                # composante continue
        base = np.exp(2j * np.pi * f * t)
        z = np.ones(taille, dtype=complex)
        for _ in range(int(RONFLEMENT_FMAX_HZ // f)):
            z *= base
            modele += np.real(2 * np.dot(tw, np.conj(z)) * z)
        ronflement[i * pas:i * pas + taille] += modele * fen
    return d - ronflement[pas:pas + len(d)], float(np.median(f0))


# --- Débruitage léger --------------------------------------------------------


def debruitage(d: "np.ndarray", sr: int, attenuation_max_db: float,
               debut_sec: float) -> Optional["np.ndarray"]:
    """Atténuation spectrale plafonnée ; None si le message n'a pas de pause.

    Bruit = spectre moyen des trames les plus calmes après debut_sec (la
    première demi-seconde porte la charge du bias). Gain de Wiener à rapport
    signal/bruit « décision dirigée » (Ephraim-Malah), borné à
    -attenuation_max_db : le souffle est abaissé sans trous ni gazouillis.
    """
    n = _echantillons(STFT_MS, sr)
    pas = _echantillons(STFT_PAS_MS, sr)
    fen = np.sqrt(_hann(n) * 2 / (n // pas))   # analyse × synthèse : somme = 1 à 75 %
    x = np.concatenate([np.zeros(n), d, np.zeros(2 * n)])
    n_trames = (len(x) - n) // pas + 1
    spectres = np.fft.rfft(np.stack([x[i * pas:i * pas + n] * fen
                                     for i in range(n_trames)]), axis=1)
    puissance = np.abs(spectres) ** 2

    premiere = int((debut_sec * sr + n) / pas)
    utiles = puissance[premiere:int((len(d) + n) / pas)]
    if len(utiles) < 10:
        return None
    energie_db = 10 * np.log10(utiles.sum(axis=1) + 1e-20)
    calmes = np.argsort(energie_db)[: max(5, int(len(utiles) * BRUIT_PART_CALME))]
    if np.percentile(energie_db, 95) - energie_db[calmes].mean() < BRUIT_ECART_MIN_DB:
        return None
    bruit = utiles[calmes].mean(axis=0) + 1e-20

    g_min = 10 ** (-attenuation_max_db / 20)
    gain = np.ones(puissance.shape[1])
    snr_prec = np.ones(puissance.shape[1])
    for i in range(n_trames):
        snr = puissance[i] / bruit
        xi = DD_ALPHA * gain ** 2 * snr_prec + (1 - DD_ALPHA) * np.maximum(snr - 1, 0)
        gain = np.maximum(xi / (1 + xi), g_min)
        spectres[i] *= gain
        snr_prec = snr

    trames = np.fft.irfft(spectres, n, axis=1) * fen
    y = np.zeros(len(x))
    for i in range(n_trames):
        y[i * pas:i * pas + n] += trames[i]
    return y[n:n + len(d)]


def gain_normalisation(d: "np.ndarray", sr: int) -> Optional[float]:
    """Gain (dB) qui amène la voix à TRAITEMENT_NIVEAU_VOIX_DBFS, plafonné.

    Niveau de la voix = 95e centile des blocs de 20 ms : robuste aux
    claquements brefs, et les pauses ne comptent pas. None si le signal est
    vide.
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
    resume = []

    if config.TRAITEMENT_ANTI_RONFLEMENT:
        d, f0 = anti_ronflement(d, sr)
        resume.append(f"ronflement secteur retiré ({f0:.2f} Hz)")
    else:
        resume.append("sans anti-ronflement")

    if config.TRAITEMENT_DEBRUITAGE_DB > 0:
        y = debruitage(d, sr, config.TRAITEMENT_DEBRUITAGE_DB,
                       config.TRAITEMENT_PROFIL_DEBUT_SEC)
        if y is None:
            resume.append("débruitage sauté (pas de pause exploitable)")
        else:
            d = y
            resume.append(f"débruitage {config.TRAITEMENT_DEBRUITAGE_DB:g} dB max")
    else:
        resume.append("débruitage désactivé")

    if config.TRAITEMENT_NORMALISATION:
        debut = int(config.TRAITEMENT_PROFIL_DEBUT_SEC * sr)
        gain = gain_normalisation(d[debut:], sr)
        if gain is not None:
            # sox travaille en mono (les deux pistes sont identiques) ; -l :
            # limiteur, les crêtes (claquement du raccroché) sont écrasées au
            # lieu d'écrêter.
            f_in, f_out = tmp / "traite.wav", tmp / "normalisation.wav"
            ecrire(f_in, d, sr, canaux=1)
            _sox(str(f_in), str(f_out), "gain", "-l", f"{gain:.1f}")
            d, _ = charger(f_out)
            resume.append(f"gain {gain:+.1f} dB")

    ecrire(sortie, d, sr)
    return ", ".join(resume)


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
