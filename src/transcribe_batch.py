"""Transcription batch des messages, post-événement uniquement (§5.5).

Le Raspberry Pi Zero 2 W (512 Mo RAM) ne permet pas de transcription en
temps réel. Ce script traite `messages/*.wav` avec whisper.cpp (modèle
`tiny` quantisé), en écrivant un `.txt` à côté de chaque WAV — jamais
d'écriture, modification ni suppression du fichier audio source.

⚠️ NE JAMAIS lancer ce script pendant l'événement : whisper.cpp est lent
sur ce matériel et entrerait en concurrence CPU/RAM avec l'enregistrement
en direct des invités. Réservé à un usage nocturne sur le Pi (service
livre-dor.service arrêté), ou à un transfert des WAV vers une machine plus
puissante. Par défaut, ce script refuse de démarrer si livre-dor.service
est actif (--force pour outrepasser, déconseillé).

Usage :
    python3 src/transcribe_batch.py --dry-run   # liste ce qui serait transcrit
    python3 src/transcribe_batch.py              # transcrit les fichiers manquants
    python3 src/transcribe_batch.py --force       # ignore la vérification livre-dor.service
"""

import argparse
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import config

logger = logging.getLogger(__name__)


def find_untranscribed_messages() -> List[Path]:
    """WAV de messages/ sans .txt associé : jamais retraité (idempotent, incrémental)."""
    if not config.MESSAGES_DIR.exists():
        return []
    return sorted(p for p in config.MESSAGES_DIR.glob("*.wav") if not p.with_suffix(".txt").exists())


def _livre_dor_service_active() -> bool:
    """True si livre-dor.service tourne actuellement (systemd absent -> False, ne bloque pas les tests/dev)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "livre-dor.service"], timeout=10, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _resample_to_16k_mono(source: Path, destination: Path) -> None:
    """whisper.cpp attend du 16 kHz mono ; nos enregistrements sont en 44,1 kHz (§4.3)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(destination)],
        capture_output=True, text=True, timeout=config.WHISPER_TIMEOUT_SEC, check=True,
    )


def _run_whisper(wav_16k_path: Path) -> str:
    result = subprocess.run(
        [config.WHISPER_BINARY, "-m", config.WHISPER_MODEL_PATH, "-f", str(wav_16k_path),
         "-l", config.WHISPER_LANGUAGE, "-nt"],
        capture_output=True, text=True, timeout=config.WHISPER_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "échec whisper.cpp (code non nul)")
    return result.stdout.strip()


def transcribe_file(wav_path: Path) -> Path:
    """Transcrit un seul WAV ; écrit le .txt à côté, sans jamais toucher au WAV source."""
    txt_path = wav_path.with_suffix(".txt")
    with tempfile.TemporaryDirectory() as tmp:
        resampled = Path(tmp) / "resampled_16k.wav"
        _resample_to_16k_mono(wav_path, resampled)
        text = _run_whisper(resampled)
    txt_path.write_text(text + "\n", encoding="utf-8")
    return txt_path


def run_batch(force: bool = False) -> dict:
    """Transcrit tous les WAV manquants ; un échec sur un fichier n'interrompt jamais le lot."""
    if not force and _livre_dor_service_active():
        message = (
            "livre-dor.service est actif : la transcription entrerait en concurrence CPU/RAM avec "
            "l'enregistrement en direct (§5.5). Arrêtez le service (`systemctl stop livre-dor.service`) "
            "ou relancez avec --force si vous savez ce que vous faites."
        )
        logger.error(message)
        return {"ok": False, "message": message, "transcribed": 0, "failed": 0, "total": 0}

    if not shutil.which(config.WHISPER_BINARY):
        message = f"Binaire whisper introuvable : {config.WHISPER_BINARY} (voir scripts/install_whisper.sh)."
        logger.error(message)
        return {"ok": False, "message": message, "transcribed": 0, "failed": 0, "total": 0}

    if not Path(config.WHISPER_MODEL_PATH).exists():
        message = f"Modèle whisper introuvable : {config.WHISPER_MODEL_PATH} (voir scripts/install_whisper.sh)."
        logger.error(message)
        return {"ok": False, "message": message, "transcribed": 0, "failed": 0, "total": 0}

    pending = find_untranscribed_messages()
    transcribed = failed = 0
    for wav_path in pending:
        try:
            txt_path = transcribe_file(wav_path)
            logger.info("Transcrit : %s -> %s", wav_path.name, txt_path.name)
            transcribed += 1
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, RuntimeError, OSError) as exc:
            logger.error("Échec de transcription de %s : %s", wav_path.name, exc)
            failed += 1
            continue

    return {
        "ok": True,
        "message": f"{transcribed} transcrit(s), {failed} échec(s), {len(pending)} traité(s) au total.",
        "transcribed": transcribed, "failed": failed, "total": len(pending),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true",
                         help="Lance même si livre-dor.service est actif (déconseillé, §5.5).")
    parser.add_argument("--dry-run", action="store_true",
                         help="Liste les fichiers qui seraient transcrits, sans rien transcrire.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        pending = find_untranscribed_messages()
        print(f"{len(pending)} fichier(s) à transcrire :")
        for path in pending:
            print(f"  - {path.name}")
        return

    print("⚠️  Ne lancez jamais ce script pendant l'événement (concurrence CPU/RAM avec "
          "l'enregistrement en direct, §5.5). Réservé à un usage nocturne ou post-événement.")
    result = run_batch(force=args.force)
    print(result["message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
