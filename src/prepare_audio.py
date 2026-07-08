"""Pré-traitement des fichiers audio (§4.2 de la spécification).

Transforme les fichiers sources bruts de audio_src/ en fichiers stéréo panés
à 100 % sur un seul canal, prêts à être joués par livre_dor.py, dans audio/ :

- tonalite.wav et bip.wav sont générés par synthèse (aucune source requise) ;
- ring_out.wav, message_generique.wav et message_N.wav (N=0..9) sont dérivés
  des fichiers correspondants placés dans audio_src/ (sonnerie.*,
  message_generique.*, message_0.* ... message_9.*) ; un chiffre sans fichier
  source est simplement ignoré (message générique utilisé en fallback par
  livre_dor.py).

Usage :
    python3 prepare_audio.py             # génère tous les fichiers dans audio/
    python3 prepare_audio.py --play-all   # rejoue chaque fichier généré (vérif panning au casque, §7.6)
"""

import argparse
import logging
from pathlib import Path

from pydub import AudioSegment
from pydub.generators import Sine

import audio_io
import config

logger = logging.getLogger(__name__)

FRAME_RATE = 44100
MESSAGE_GAIN_DB = -9
DIAL_TONE_DURATION_MS = 30000


def _pan_and_gain(segment: AudioSegment, channel: str, gain_db: float) -> AudioSegment:
    """Retourne une version stéréo de segment, panée à 100 % sur 'left' ou 'right', avec gain_db appliqué."""
    mono = segment.set_channels(1).set_frame_rate(FRAME_RATE).apply_gain(gain_db)
    silence = AudioSegment.silent(duration=len(mono), frame_rate=FRAME_RATE)
    if channel == "left":
        return AudioSegment.from_mono_audiosegments(mono, silence)
    if channel == "right":
        return AudioSegment.from_mono_audiosegments(silence, mono)
    raise ValueError(f"canal inconnu : {channel!r}")


def _generate_dial_tone() -> AudioSegment:
    """Tonalité d'invitation à numéroter : mélange 440 Hz + 480 Hz (§1.2)."""
    tone_a = Sine(440).to_audio_segment(duration=DIAL_TONE_DURATION_MS, volume=-6)
    tone_b = Sine(480).to_audio_segment(duration=DIAL_TONE_DURATION_MS, volume=-6)
    return tone_a.overlay(tone_b)


def _generate_beep(duration_ms: int = 400, freq: int = 1000) -> AudioSegment:
    return Sine(freq).to_audio_segment(duration=duration_ms, volume=-6)


def _process_source(name: str, target: Path, channel: str, gain_db: float) -> bool:
    """Traite audio_src/<name>.* -> target. Retourne False si aucune source trouvée."""
    candidates = sorted(config.AUDIO_SRC_DIR.glob(f"{name}.*"))
    if not candidates:
        logger.warning("Source absente pour %r (attendue dans %s) : %s non généré.",
                        name, config.AUDIO_SRC_DIR, target.name)
        return False
    source_path = candidates[0]
    segment = AudioSegment.from_file(source_path)
    stereo = _pan_and_gain(segment, channel, gain_db)
    target.parent.mkdir(parents=True, exist_ok=True)
    stereo.export(target, format="wav")
    logger.info("Généré : %s (source %s, canal %s, gain %.1f dB)", target, source_path.name, channel, gain_db)
    return True


def prepare_all() -> None:
    config.ensure_directories()

    tonalite = _pan_and_gain(_generate_dial_tone(), "left", -12)
    tonalite.export(config.TONALITE_WAV, format="wav")
    logger.info("Généré (synthèse) : %s", config.TONALITE_WAV)

    bip = _pan_and_gain(_generate_beep(), "left", -6)
    bip.export(config.BIP_WAV, format="wav")
    logger.info("Généré (synthèse) : %s", config.BIP_WAV)

    _process_source("sonnerie", config.RING_OUT_WAV, "right", 0)
    _process_source("message_generique", config.MESSAGE_GENERIQUE_WAV, "left", MESSAGE_GAIN_DB)
    for digit in range(10):
        _process_source(f"message_{digit}", config.message_wav(digit), "left", MESSAGE_GAIN_DB)


def play_all() -> None:
    """Rejoue chaque fichier généré, pour vérifier manuellement le panning (checklist §7.6, point 4)."""
    generated = sorted(config.AUDIO_DIR.glob("*.wav"))
    if not generated:
        print(f"Aucun fichier dans {config.AUDIO_DIR} — lancez d'abord `python3 prepare_audio.py`.")
        return
    for path in generated:
        input(f"Entrée pour jouer {path.name} (Ctrl+C pour arrêter cette lecture)... ")
        result = audio_io.play(path, should_continue=lambda: True)
        print(f"  -> {result}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--play-all", action="store_true",
                         help="Rejoue chaque fichier généré au lieu de (re)générer.")
    args = parser.parse_args()

    if args.play_all:
        play_all()
    else:
        prepare_all()


if __name__ == "__main__":
    main()
