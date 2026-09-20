"""Pré-traitement des fichiers audio (§4.2 de la spécification).

Transforme les fichiers sources bruts de `audio_src/` (mp3, m4a, wav… tels que
déposés depuis un smartphone) en WAV **48 kHz, 16 bits, stéréo avec les deux
pistes identiques**, prêts à être joués par `livre_dor.py`, dans `audio/`.

Pourquoi ce format :

- **stéréo L = R** parce que le line out alimente un unique haut-parleur mono
  de sonnerie, qui lit la piste gauche, tandis que la sortie casque alimente
  un écouteur par côté (combiné à gauche, écouteur secondaire à droite). Les
  deux doivent recevoir le même signal au même niveau. Il n'y a donc plus
  aucun panning : c'est le codec qui sélectionne la sortie (cf. `alsa_io`) ;
- **48 kHz** parce que c'est la cadence exigée par RNNoise, et que le full
  duplex impose que lecture et capture partagent cadence et format (§4.3).

Contenu généré :

- `tonalite.wav` et `bip.wav` sont produits par synthèse (aucune source) ;
- `ring_out.wav`, `message_generique.wav`, `message_N.wav` (N=0..9) et
  `aucun_message.wav` viennent des fichiers de `audio_src/` choisis dans le
  dashboard, ou, à défaut, du fichier portant le nom du rôle
  (`audio_src/sonnerie.*`) — cf. `audio_config.source_for()`.

Usage :
    python3 prepare_audio.py                 # (re)génère tout dans audio/
    python3 prepare_audio.py --role sonnerie  # un seul rôle
    python3 prepare_audio.py --play-all       # rejoue chaque fichier généré (§7.6)
"""

import argparse
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from pydub.generators import Sine

import audio_config
import audio_io
import config

logger = logging.getLogger(__name__)

FRAME_RATE = config.AUDIO_RATE_HZ      # 48 000 Hz
SAMPLE_WIDTH = 2                       # 16 bits (S16_LE)

MESSAGE_GAIN_DB = -9
RING_GAIN_DB = 0
TONALITE_GAIN_DB = -12
BIP_GAIN_DB = -6

DIAL_TONE_DURATION_MS = 30000

# Bip de répondeur classique : plus long et plus grave que l'ancien
# (1000 Hz / 400 ms), encadré de silence pour bien le détacher de la fin du
# message des mariés et du début de l'enregistrement.
BIP_FREQ_HZ = 800
BIP_DURATION_MS = 600
BIP_SILENCE_BEFORE_MS = 250
BIP_SILENCE_AFTER_MS = 250
# Fondus très courts : sans eux, un signal carré en début et fin de bip
# produit un clic net, très audible dans un écouteur de combiné.
BIP_FADE_MS = 15

# Silence de tête sur chaque fichier converti : absorbe le « pop » que produit
# l'activation de l'ampli lors de la commutation de sortie du codec.
LEAD_SILENCE_MS = 20

# Garde-fou mémoire : pydub charge tout le fichier décodé en RAM et le Pi Zero
# 2 W n'a que 512 Mo. 5 minutes de message sont déjà bien au-delà du besoin.
MAX_SOURCE_DURATION_SEC = 300

# Le dashboard tourne en threaded=True : deux conversions simultanées feraient
# ramer le Pi. Un verrou non bloquant permet de répondre « occupé » plutôt que
# d'empiler les décodages.
_conversion_lock = threading.Lock()


def roles() -> Dict[str, Tuple[Path, float]]:
    """rôle -> (fichier cible, gain dB).

    Fonction et non constante de module : tests/harness.py réassigne
    config.RING_OUT_WAV & co. à l'entrée de chaque scénario, une table figée à
    l'import écrirait dans le vrai dossier audio/.
    """
    table: Dict[str, Tuple[Path, float]] = {}
    for role in audio_config.ROLE_NAMES:
        cible = audio_config.target_for(role)
        if cible is None:
            continue
        gain = RING_GAIN_DB if role == audio_config.ROLE_SONNERIE else MESSAGE_GAIN_DB
        table[role] = (cible, gain)
    return table


def output_for(role: str) -> str:
    """Sortie du codec sur laquelle ce rôle doit être entendu (§4.1)."""
    if role == audio_config.ROLE_SONNERIE:
        return config.AUDIO_OUTPUT_SONNERIE
    return config.AUDIO_OUTPUT_COMBINE


# --- Traitement du signal ------------------------------------------------


def _to_dual_mono(segment: AudioSegment, gain_db: float = 0.0,
                  lead_silence_ms: int = LEAD_SILENCE_MS) -> AudioSegment:
    """Rend le segment stéréo avec L = R, à 48 kHz et 16 bits.

    Ordre volontaire : downmix mono d'abord (un rééchantillonnage de moins),
    gain en dernier (le clipping s'évalue sur les échantillons finaux).
    """
    mono = (segment.set_channels(1)
                   .set_frame_rate(FRAME_RATE)
                   .set_sample_width(SAMPLE_WIDTH)
                   .apply_gain(gain_db))
    if lead_silence_ms:
        mono = AudioSegment.silent(duration=lead_silence_ms, frame_rate=FRAME_RATE) + mono
    return AudioSegment.from_mono_audiosegments(mono, mono)


def _generate_dial_tone() -> AudioSegment:
    """Tonalité d'invitation à numéroter : mélange 440 Hz + 480 Hz (§1.2)."""
    tone_a = Sine(440, sample_rate=FRAME_RATE).to_audio_segment(
        duration=DIAL_TONE_DURATION_MS, volume=-6)
    tone_b = Sine(480, sample_rate=FRAME_RATE).to_audio_segment(
        duration=DIAL_TONE_DURATION_MS, volume=-6)
    return tone_a.overlay(tone_b)


def _generate_beep() -> AudioSegment:
    """Bip de répondeur joué après le message des mariés, avant l'enregistrement."""
    bip = (Sine(BIP_FREQ_HZ, sample_rate=FRAME_RATE)
           .to_audio_segment(duration=BIP_DURATION_MS, volume=-6)
           .fade_in(BIP_FADE_MS)
           .fade_out(BIP_FADE_MS))
    return (AudioSegment.silent(duration=BIP_SILENCE_BEFORE_MS, frame_rate=FRAME_RATE)
            + bip
            + AudioSegment.silent(duration=BIP_SILENCE_AFTER_MS, frame_rate=FRAME_RATE))


def _export_atomic(segment: AudioSegment, target: Path) -> None:
    """Écrit le WAV via un fichier temporaire puis os.replace().

    Indispensable : une reconversion lancée depuis le dashboard peut tomber
    pendant que livre_dor.py joue ce même fichier — sans remplacement
    atomique, aplay lirait un WAV tronqué.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        segment.export(tmp_path, format="wav")
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


# --- Conversion d'un rôle ------------------------------------------------


def convert_role(role: str) -> dict:
    """Convertit la source du rôle vers son fichier de audio/.

    Retourne {"ok", "role", "source", "cible", "message"}. Un rôle sans source
    n'est pas une erreur : c'est le cas normal d'un chiffre non attribué, que
    livre_dor.py sert avec le message générique.
    """
    table = roles()
    if role not in table:
        return {"ok": False, "role": role, "source": None, "cible": None,
                "message": f"Rôle inconnu : {role}"}

    target, gain_db = table[role]
    source = audio_config.source_for(role)
    if source is None:
        return {"ok": True, "role": role, "source": None, "cible": target.name,
                "message": "aucune source : rôle ignoré"}

    try:
        segment = AudioSegment.from_file(source)
    except (CouldntDecodeError, OSError, ValueError, IndexError) as exc:
        # pydub recopie toute la sortie de ffmpeg (bannière de version
        # comprise) dans le message : on n'en garde que la première ligne,
        # seule utile dans un log ou dans le dashboard.
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        logger.warning("Décodage impossible pour %s (%s) : %s", role, source.name, detail)
        return {"ok": False, "role": role, "source": source.name, "cible": target.name,
                "message": f"fichier illisible ou format non reconnu ({detail})"}

    if len(segment) > MAX_SOURCE_DURATION_SEC * 1000:
        return {"ok": False, "role": role, "source": source.name, "cible": target.name,
                "message": (f"source trop longue ({len(segment) // 1000} s, maximum "
                            f"{MAX_SOURCE_DURATION_SEC} s)")}

    try:
        _export_atomic(_to_dual_mono(segment, gain_db), target)
    except (OSError, ValueError) as exc:
        logger.warning("Écriture impossible pour %s : %s", target, exc)
        return {"ok": False, "role": role, "source": source.name, "cible": target.name,
                "message": f"écriture impossible ({exc})"}

    logger.info("Généré : %s (source %s, gain %.1f dB)", target, source.name, gain_db)
    return {"ok": True, "role": role, "source": source.name, "cible": target.name,
            "message": f"converti depuis {source.name}"}


def generate_synthesized() -> None:
    """(Re)génère les deux fichiers produits par synthèse : tonalité et bip."""
    _export_atomic(_to_dual_mono(_generate_dial_tone(), TONALITE_GAIN_DB),
                   config.TONALITE_WAV)
    logger.info("Généré (synthèse) : %s", config.TONALITE_WAV)
    # lead_silence_ms=0 : le bip porte déjà son propre silence de tête.
    _export_atomic(_to_dual_mono(_generate_beep(), BIP_GAIN_DB, lead_silence_ms=0),
                   config.BIP_WAV)
    logger.info("Généré (synthèse) : %s", config.BIP_WAV)


def prepare_all(noms: Optional[Iterable[str]] = None, synthese: bool = True) -> dict:
    """Convertit tous les rôles demandés (tous par défaut).

    Un rôle en échec n'interrompt jamais le lot : une source illisible parmi
    douze ne doit pas laisser les onze autres non générées.
    """
    config.ensure_directories()
    resultats = {}

    if synthese:
        try:
            generate_synthesized()
        except (OSError, ValueError) as exc:
            logger.exception("Génération des fichiers de synthèse impossible")
            resultats["_synthese"] = {"ok": False, "message": str(exc)}

    for role in (list(noms) if noms is not None else audio_config.ROLE_NAMES):
        resultats[role] = convert_role(role)

    erreurs = {role: r["message"] for role, r in resultats.items() if not r["ok"]}
    generes = [role for role, r in resultats.items() if r["ok"] and r.get("source")]
    ignores = [role for role, r in resultats.items() if r["ok"] and not r.get("source")]

    return {"ok": not erreurs, "generes": generes, "ignores": ignores,
            "erreurs": erreurs, "resultats": resultats}


def prepare_all_locked(noms: Optional[Iterable[str]] = None) -> dict:
    """prepare_all() sous verrou non bloquant ; « occupé » plutôt qu'une file d'attente."""
    if not _conversion_lock.acquire(blocking=False):
        return {"ok": False, "occupe": True, "generes": [], "ignores": [],
                "erreurs": {}, "resultats": {},
                "message": "Une conversion est déjà en cours."}
    try:
        return prepare_all(noms)
    finally:
        _conversion_lock.release()


def conversion_en_cours() -> bool:
    """Une conversion tient-elle le verrou en ce moment ?"""
    if _conversion_lock.acquire(blocking=False):
        _conversion_lock.release()
        return False
    return True


def play_all() -> None:
    """Rejoue chaque fichier généré sur sa sortie (vérification du câblage, §7.6).

    Remplace l'ancienne vérification du panning : ce qu'on contrôle désormais,
    c'est que la sonnerie sort du haut-parleur de sonnerie et que tout le
    reste sort des deux écouteurs, au même niveau.
    """
    cibles = {target.name: role for role, (target, _) in roles().items()}
    generated = sorted(config.AUDIO_DIR.glob("*.wav"))
    if not generated:
        print(f"Aucun fichier dans {config.AUDIO_DIR} — lancez d'abord "
              "`python3 src/prepare_audio.py`.")
        return
    for path in generated:
        role = cibles.get(path.name)
        sortie = output_for(role) if role else config.AUDIO_OUTPUT_COMBINE
        input(f"Entrée pour jouer {path.name} sur {sortie} (Ctrl+C pour arrêter)... ")
        result = audio_io.play(path, should_continue=lambda: True, output=sortie)
        print(f"  -> {result}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--play-all", action="store_true",
                        help="Rejoue chaque fichier généré au lieu de (re)générer.")
    parser.add_argument("--role", metavar="NOM", choices=audio_config.ROLE_NAMES,
                        help="Ne (re)convertit qu'un seul rôle.")
    args = parser.parse_args()

    if args.play_all:
        play_all()
        return

    if args.role:
        config.ensure_directories()
        resultat = convert_role(args.role)
        print(f"{args.role} : {resultat['message']}")
        raise SystemExit(0 if resultat["ok"] else 1)

    rapport = prepare_all()
    for role, message in rapport["erreurs"].items():
        print(f"ERREUR {role} : {message}")
    print(f"{len(rapport['generes'])} fichier(s) généré(s), "
          f"{len(rapport['ignores'])} rôle(s) sans source.")
    raise SystemExit(0 if rapport["ok"] else 1)


if __name__ == "__main__":
    main()
