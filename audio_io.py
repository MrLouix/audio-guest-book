"""Primitives de lecture (aplay) et d'enregistrement (arecord) audio.

Bas niveau, réutilisé par livre_dor.py (Sprint 2+) : chaque appel lance un
sous-processus ALSA et le surveille via un callback should_continue(), pour
permettre une interruption immédiate (raccroché) et éviter les processus
orphelins (§4.1, §7.2 de la spécification).
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 0.1
TERMINATE_GRACE_SEC = 1.0


def _terminate(proc: subprocess.Popen, grace_sec: float = TERMINATE_GRACE_SEC) -> None:
    """Arrête proprement un sous-processus : SIGTERM, puis SIGKILL si besoin."""
    if proc.poll() is not None:
        return
    proc.terminate()
    deadline = time.monotonic() + grace_sec
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def play(path: Path, should_continue: Callable[[], bool],
         poll_interval: float = POLL_INTERVAL_SEC,
         timeout_sec: Optional[float] = None) -> str:
    """Joue un fichier WAV sur la carte son configurée.

    should_continue() est interrogé toutes les poll_interval secondes ; dès
    qu'il renvoie False (ex. raccroché détecté), la lecture est interrompue
    immédiatement. Retourne "completed", "interrupted" ou "error".
    """
    if not path.exists():
        logger.error("Fichier audio introuvable : %s", path)
        return "error"

    cmd = ["aplay", "-D", config.SOUND_CARD, str(path)]
    logger.debug("Lecture : %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError:
        logger.exception("Impossible de lancer aplay")
        return "error"

    start = time.monotonic()
    result = "completed"
    while True:
        if proc.poll() is not None:
            break
        if not should_continue():
            result = "interrupted"
            _terminate(proc)
            break
        if timeout_sec is not None and (time.monotonic() - start) > timeout_sec:
            logger.warning("Timeout dépassé pendant la lecture de %s", path)
            result = "error"
            _terminate(proc)
            break
        time.sleep(poll_interval)

    if result == "completed" and proc.returncode != 0:
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        logger.error("aplay a échoué (code %s) sur %s : %s", proc.returncode, path, stderr.strip())
        result = "error"

    return result


def record(path: Path, max_duration_sec: int, should_continue: Callable[[], bool],
           poll_interval: float = POLL_INTERVAL_SEC) -> str:
    """Enregistre directement vers path (WAV mono 44,1 kHz).

    should_continue() est interrogé toutes les poll_interval secondes ; dès
    qu'il renvoie False (raccroché), l'enregistrement est arrêté immédiatement
    et le fichier déjà écrit reste valide. max_duration_sec est aussi passé à
    arecord (-d) comme filet de sécurité si la boucle appelante se bloquait.
    Retourne "completed", "interrupted" ou "error".
    """
    cmd = [
        "arecord",
        "-D", config.SOUND_CARD,
        "-f", "S16_LE",
        "-c", "1",
        "-r", "44100",
        "-d", str(max_duration_sec),
        str(path),
    ]
    logger.debug("Enregistrement : %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError:
        logger.exception("Impossible de lancer arecord")
        return "error"

    result = "completed"
    while True:
        if proc.poll() is not None:
            break
        if not should_continue():
            result = "interrupted"
            _terminate(proc)
            break
        time.sleep(poll_interval)

    if result == "completed" and proc.returncode != 0:
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        logger.error("arecord a échoué (code %s) sur %s : %s", proc.returncode, path, stderr.strip())
        result = "error"

    return result


def sound_card_available(sound_card: Optional[str] = None) -> bool:
    """Vérifie que le périphérique ALSA configuré est bien listé par `aplay -l` (§7.2)."""
    sound_card = sound_card or config.SOUND_CARD
    try:
        card_index = sound_card.split(":")[1].split(",")[0]
    except IndexError:
        return False
    try:
        output = subprocess.run(
            ["aplay", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    return f"card {card_index}:" in output


def _cli() -> None:
    """Test manuel : `python3 audio_io.py play audio/tonalite.wav` ou `record test.wav --duration 5`."""
    import argparse
    import signal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Test manuel de lecture/enregistrement audio.")
    sub = parser.add_subparsers(dest="command", required=True)

    play_parser = sub.add_parser("play", help="Joue un fichier WAV jusqu'à la fin ou Ctrl+C.")
    play_parser.add_argument("path", type=Path)

    record_parser = sub.add_parser("record", help="Enregistre un WAV pendant N secondes ou jusqu'à Ctrl+C.")
    record_parser.add_argument("path", type=Path)
    record_parser.add_argument("--duration", type=int, default=10)

    args = parser.parse_args()

    if args.command == "play":
        result = play(args.path, should_continue=lambda: True)
        print(f"Résultat : {result}")
    else:
        stop_requested = False

        def handler(signum, frame):
            nonlocal stop_requested
            stop_requested = True

        signal.signal(signal.SIGINT, handler)
        print(f"Enregistrement de {args.path} pendant {args.duration}s (Ctrl+C pour arrêter avant)...")
        result = record(args.path, max_duration_sec=args.duration,
                         should_continue=lambda: not stop_requested)
        print(f"Résultat : {result}")


if __name__ == "__main__":
    _cli()
