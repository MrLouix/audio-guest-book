"""Test de charge (Sprint 12, recette finale, §7.6) : plusieurs cycles
décroché/composition/enregistrement/raccroché à la suite, pour vérifier
l'absence de fuite de sous-processus (aplay/arecord orphelins) ou de
fichiers.

À exécuter sur le matériel final assemblé (carte son branchée, fichiers
audio déjà préparés via prepare_audio.py) avant l'événement — jamais
pendant. Les enregistrements de test sont écrits dans un dossier temporaire
séparé (jamais dans messages/), automatiquement supprimé à la fin : ce
script ne laisse aucune trace parmi les vrais messages des invités.

Usage :
    python3 src/load_test.py --cycles 20
"""

import argparse
import logging
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import audio_io
import config
import gpio_io
import livre_dor

logger = logging.getLogger(__name__)

CYCLE_SETTLE_SEC = 0.2
POST_HANGUP_SETTLE_SEC = 0.3


def _count_processes(name: str) -> int:
    """Nombre de processus nommés `name` actuellement en vie.

    Retourne 0 si `pgrep` est indisponible plutôt que d'échouer : cette
    mesure est une aide au diagnostic, pas une condition bloquante.
    """
    try:
        result = subprocess.run(["pgrep", "-c", "-x", name], capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return 0
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return 0


def _dial_digit(inputs: gpio_io.PhoneInputs, digit: int, pulse_gap: float = 0.06) -> None:
    n_pulses = 10 if digit == 0 else digit
    inputs.set_dial_active(True)
    for _ in range(n_pulses):
        time.sleep(pulse_gap)
        inputs.register_pulse()
    time.sleep(pulse_gap)
    inputs.set_dial_active(False)


def run_load_test(cycles: int, hangup_after_sec: float = 1.0) -> dict:
    """Simule `cycles` appels complets ; retourne des compteurs de diagnostic."""
    tmp_messages_dir = Path(tempfile.mkdtemp(prefix="livredor_loadtest_"))
    original_messages_dir = config.MESSAGES_DIR
    config.MESSAGES_DIR = tmp_messages_dir

    aplay_before = _count_processes("aplay")
    arecord_before = _count_processes("arecord")

    inputs = gpio_io.PhoneInputs()
    machine = livre_dor.GuestBookStateMachine(inputs)
    thread = threading.Thread(target=machine.run_forever, daemon=True)
    thread.start()

    try:
        for i in range(cycles):
            digit = random.randint(0, 9)
            logger.info("Cycle %d/%d : chiffre %d", i + 1, cycles, digit)
            inputs.set_hook(True)
            time.sleep(CYCLE_SETTLE_SEC)
            _dial_digit(inputs, digit)
            time.sleep(hangup_after_sec)
            inputs.set_hook(False)
            time.sleep(POST_HANGUP_SETTLE_SEC)
    finally:
        aplay_after = _count_processes("aplay")
        arecord_after = _count_processes("arecord")
        recordings = list(tmp_messages_dir.glob("*.wav"))
        shutil.rmtree(tmp_messages_dir, ignore_errors=True)
        config.MESSAGES_DIR = original_messages_dir

    return {
        "cycles": cycles,
        "fichiers_crees": len(recordings),
        "aplay_orphelins": max(0, aplay_after - aplay_before),
        "arecord_orphelins": max(0, arecord_after - arecord_before),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=20,
                         help="Nombre de cycles décroché/composition/enregistrement/raccroché à simuler.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not audio_io.sound_card_available():
        print(f"ATTENTION : carte son {config.SOUND_CARD} introuvable — les lectures/enregistrements "
              "échoueront, mais ce test peut quand même détecter des fuites de sous-processus.\n")

    result = run_load_test(args.cycles)

    print(f"Cycles simulés    : {result['cycles']}")
    print(f"Fichiers créés    : {result['fichiers_crees']} (attendu : {result['cycles']})")
    print(f"aplay orphelins   : {result['aplay_orphelins']}")
    print(f"arecord orphelins : {result['arecord_orphelins']}")

    ok = (
        result["fichiers_crees"] == result["cycles"]
        and result["aplay_orphelins"] == 0
        and result["arecord_orphelins"] == 0
    )
    print("\nRÉSULTAT : " + ("OK, aucune fuite détectée." if ok else "ÉCHEC — fuite détectée, voir ci-dessus."))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
