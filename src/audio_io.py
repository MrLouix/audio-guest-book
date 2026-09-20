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
         timeout_sec: Optional[float] = None,
         device: Optional[str] = None,
         output: Optional[str] = None) -> str:
    """Joue un fichier WAV sur la carte son configurée.

    should_continue() est interrogé toutes les poll_interval secondes ; dès
    qu'il renvoie False (ex. raccroché détecté), la lecture est interrompue
    immédiatement. Retourne "completed", "interrupted" ou "error".

    device permet de surcharger le périphérique ALSA (défaut : SOUND_CARD) :
    échappatoire de routage laissée au mode restitution (§5.7), qui peut ainsi
    pointer un périphérique ALSA `route` sans modification de code.

    output commute la sortie du codec avant la lecture (§4.1) : "lineout"
    pour la sonnerie, "headphone" pour le combiné et l'écouteur secondaire.
    La commutation est faite AVANT l'ouverture du PCM — la faire après
    mettrait les premières dizaines de millisecondes sur le mauvais
    haut-parleur. Un échec de commutation n'empêche jamais la lecture.
    """
    if not path.exists():
        logger.error("Fichier audio introuvable : %s", path)
        return "error"

    if output:
        # Import local : alsa_io importe ce module (pour card_index), un
        # import en tête de fichier serait circulaire.
        import alsa_io
        alsa_io.select_output(output)

    cmd = ["aplay", "-D", device or config.SOUND_CARD, str(path)]
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
    """Enregistre directement vers path (WAV stéréo 48 kHz, 16 bits).

    48 kHz, et non 44,1 : c'est la cadence exigée par RNNoise, et le full
    duplex impose que capture et lecture partagent cadence et format (§4.3).
    Le micro est sur l'entrée Aux gauche, dupliquée sur les deux canaux DAI
    par scripts/audio-setup.sh (numids 89 et 90) — les deux pistes portent
    donc le même signal.

    should_continue() est interrogé toutes les poll_interval secondes ; dès
    qu'il renvoie False (raccroché), l'enregistrement est arrêté immédiatement
    et le fichier déjà écrit reste valide. max_duration_sec est aussi passé à
    arecord (-d) comme filet de sécurité si la boucle appelante se bloquait.
    Retourne "completed", "interrupted" ou "error".
    """
    cmd = [
        "arecord",
        "-D", config.SOUND_CARD,
        "-f", config.AUDIO_SAMPLE_FORMAT,
        "-c", str(config.RECORD_CHANNELS),
        "-r", str(config.RECORD_RATE_HZ),
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


def card_index(sound_card: Optional[str] = None) -> Optional[str]:
    """Index de carte ALSA extrait de « plughw:1,0 » -> « 1 » ; None si illisible.

    Utilisé par sound_card_available() et par alsa_io, qui le passe à
    scripts/audio-setup.sh : le numéro de carte reste ainsi dérivé de
    SOUND_CARD, sans constante à tenir à jour en double.
    """
    sound_card = sound_card or config.SOUND_CARD
    try:
        return sound_card.split(":")[1].split(",")[0]
    except IndexError:
        return None


def sound_card_available(sound_card: Optional[str] = None) -> bool:
    """Vérifie que le périphérique ALSA configuré est bien listé par `aplay -l` (§7.2)."""
    index = card_index(sound_card)
    if index is None:
        return False
    try:
        output = subprocess.run(
            ["aplay", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    return f"card {index}:" in output


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
