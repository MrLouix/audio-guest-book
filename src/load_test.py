"""Test de charge (Sprint 12, recette finale, §7.6) : plusieurs cycles
décroché/composition/enregistrement/raccroché à la suite, pour vérifier
l'absence de fuite de sous-processus (aplay/arecord orphelins) ou de
fichiers.

À exécuter sur le matériel final assemblé (carte son branchée, fichiers
audio déjà préparés via prepare_audio.py) avant l'événement — jamais
pendant. Les enregistrements de test sont écrits dans un dossier temporaire
séparé (jamais dans messages/), automatiquement supprimé à la fin : ce
script ne laisse aucune trace parmi les vrais messages des invités.

Chaque cycle suit la machine à états plutôt qu'un minuteur : il attend
qu'elle soit revenue en attente avant de décrocher, qu'elle soit entrée en
enregistrement (message des mariés et bip joués en entier) avant de laisser
parler, puis qu'elle soit revenue en attente après le raccroché. Un cycle
dure donc à peu près message + bip + --record-sec + 2 s : comptez quelques
minutes pour 20 cycles.

Le test tourne toujours en mode mariage, quel que soit le mode réel : il lit
un mode_config.json temporaire, et celui du dépôt n'est pas modifié.

Usage :
    python3 src/load_test.py --cycles 20
"""

import argparse
import json
import logging
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterable

import audio_io
import config
import fichiers
import gpio_io
import livre_dor
import mode_io

logger = logging.getLogger(__name__)

STATE_POLL_SEC = 0.05
# Après l'entrée en décroché, la machine purge le cadran (reset_dial) : on
# laisse passer ce moment avant de composer, sinon le chiffre est perdu.
CYCLE_SETTLE_SEC = 0.2
DECROCHE_TIMEOUT_SEC = 5.0
# Délai accordé au-delà de la durée du message et du bip pour atteindre
# l'enregistrement : tonalité coupée, chiffre validé, commutation de sortie.
ENREGISTREMENT_MARGE_SEC = 10.0
# Retour en attente après le raccroché : confirmation du raccroché, arrêt
# d'arecord (SIGTERM puis SIGKILL après TERMINATE_GRACE_SEC).
RETOUR_ATTENTE_TIMEOUT_SEC = 10.0
DEFAULT_RECORD_SEC = 2.5


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


def _attendre_etat(machine: livre_dor.GuestBookStateMachine, etats: Iterable[str],
                   timeout_sec: float) -> bool:
    """Attend que la machine soit dans l'un des `etats` ; False si timeout_sec est dépassé."""
    etats = tuple(etats)
    deadline = time.monotonic() + timeout_sec
    while machine.state not in etats:
        if time.monotonic() >= deadline:
            return False
        time.sleep(STATE_POLL_SEC)
    return True


def _duree_avant_enregistrement(digit: int) -> float:
    """Durée du message associé à `digit` et du bip, que le parcours joue avant d'enregistrer.

    Reprend le repli de livre_dor.message_path_for_digit sans son log, qui
    répéterait « message générique utilisé » à chaque cycle.
    """
    message = config.message_wav(digit)
    if not message.exists():
        message = config.MESSAGE_GENERIQUE_WAV
    return livre_dor.wav_duration_sec(message) + livre_dor.wav_duration_sec(config.BIP_WAV)


def run_load_test(cycles: int, record_sec: float = DEFAULT_RECORD_SEC) -> dict:
    """Simule `cycles` appels complets ; retourne des compteurs de diagnostic."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="livredor_loadtest_"))
    tmp_messages_dir = tmp_dir / "messages"
    tmp_messages_dir.mkdir()
    saved = {
        "MESSAGES_DIR": config.MESSAGES_DIR,
        "MODE_CONFIG_FILE": config.MODE_CONFIG_FILE,
        "STATUS_FILE": config.STATUS_FILE,
        "RING_INTERVAL_SEC": config.RING_INTERVAL_SEC,
    }
    config.MESSAGES_DIR = tmp_messages_dir
    # Mode mariage forcé sans toucher au vrai mode_config.json : en
    # restitution, aucun cycle n'enregistrerait (§5.7).
    config.MODE_CONFIG_FILE = tmp_dir / "mode_config.json"
    config.MODE_CONFIG_FILE.write_text(json.dumps({"restitution": False}), encoding="utf-8")
    mode_io.invalidate_cache()
    # status.json est écrit à chaque changement d'état : le rediriger évite de
    # brouiller le dashboard pendant le test.
    config.STATUS_FILE = tmp_dir / "status.json"
    # Pas de sonnerie périodique au milieu d'un cycle.
    config.RING_INTERVAL_SEC = 0
    logger.info("Test de charge forcé en mode mariage (le mode réel reste inchangé).")

    aplay_before = _count_processes("aplay")
    arecord_before = _count_processes("arecord")

    inputs = gpio_io.PhoneInputs()
    machine = livre_dor.GuestBookStateMachine(inputs)
    thread = threading.Thread(target=machine.run_forever, daemon=True)
    thread.start()

    cycles_bloques = 0
    try:
        for i in range(cycles):
            if not _attendre_etat(machine, [livre_dor.STATE_ATTENTE], RETOUR_ATTENTE_TIMEOUT_SEC):
                logger.error("Cycle %d/%d : machine toujours en %s, cycle sauté.",
                             i + 1, cycles, machine.state)
                cycles_bloques += 1
                continue
            digit = random.randint(0, 9)
            logger.info("Cycle %d/%d : chiffre %d", i + 1, cycles, digit)
            inputs.set_hook(True)
            if _attendre_etat(machine, [livre_dor.STATE_DECROCHE], DECROCHE_TIMEOUT_SEC):
                time.sleep(CYCLE_SETTLE_SEC)
                _dial_digit(inputs, digit)
                timeout = _duree_avant_enregistrement(digit) + ENREGISTREMENT_MARGE_SEC
            else:
                timeout = 0.0
            if timeout and _attendre_etat(machine, [livre_dor.STATE_ENREGISTREMENT], timeout):
                time.sleep(record_sec)
            else:
                logger.error("Cycle %d/%d : enregistrement non atteint (état : %s).",
                             i + 1, cycles, machine.state)
                cycles_bloques += 1
            inputs.set_hook(False)
            if not _attendre_etat(machine, [livre_dor.STATE_ATTENTE], RETOUR_ATTENTE_TIMEOUT_SEC):
                logger.error("Cycle %d/%d : pas de retour en attente après le raccroché (état : %s).",
                             i + 1, cycles, machine.state)
                cycles_bloques += 1
    finally:
        inputs.set_hook(False)
        _attendre_etat(machine, [livre_dor.STATE_ATTENTE], RETOUR_ATTENTE_TIMEOUT_SEC)
        machine.request_stop()
        thread.join(timeout=5.0)
        # Un aplay/arecord interrompu peut mettre jusqu'à TERMINATE_GRACE_SEC à
        # disparaître : compter avant, c'est prendre un arrêt en cours pour un
        # orphelin.
        time.sleep(audio_io.TERMINATE_GRACE_SEC + 0.5)
        aplay_after = _count_processes("aplay")
        arecord_after = _count_processes("arecord")
        recordings = list(tmp_messages_dir.glob("*.wav"))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        for name, value in saved.items():
            setattr(config, name, value)
        mode_io.invalidate_cache()

    return {
        "cycles": cycles,
        "cycles_bloques": cycles_bloques,
        "fichiers_crees": len(recordings),
        "aplay_orphelins": max(0, aplay_after - aplay_before),
        "arecord_orphelins": max(0, arecord_after - arecord_before),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=20,
                         help="Nombre de cycles décroché/composition/enregistrement/raccroché à simuler.")
    parser.add_argument("--record-sec", type=float, default=DEFAULT_RECORD_SEC,
                         help="Durée de chaque enregistrement simulé, en secondes.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=fichiers.FORMAT_JOURNAL)

    if not audio_io.sound_card_available():
        print(f"ATTENTION : carte son {config.SOUND_CARD} introuvable — les lectures/enregistrements "
              "échoueront, mais ce test peut quand même détecter des fuites de sous-processus.\n")

    result = run_load_test(args.cycles, record_sec=args.record_sec)

    print(f"Cycles simulés    : {result['cycles']}")
    print(f"Cycles bloqués    : {result['cycles_bloques']}")
    print(f"Fichiers créés    : {result['fichiers_crees']} (attendu : {result['cycles']})")
    print(f"aplay orphelins   : {result['aplay_orphelins']}")
    print(f"arecord orphelins : {result['arecord_orphelins']}")

    ok = (
        result["cycles_bloques"] == 0
        and result["fichiers_crees"] == result["cycles"]
        and result["aplay_orphelins"] == 0
        and result["arecord_orphelins"] == 0
    )
    print("\nRÉSULTAT : " + ("OK, aucune fuite détectée." if ok else "ÉCHEC — voir ci-dessus."))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
