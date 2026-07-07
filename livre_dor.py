"""Script principal du livre d'or téléphonique — machine à états (§5.1).

Sprint 2 : scénario nominal (appel sortant) uniquement — attente → décroché
(tonalité) → numérotation → lecture du message → enregistrement → retour à
l'attente. La sonnerie, le scénario « appel entrant » et les surcouches de
fiabilité (status.json, watchdog, contrôle d'espace disque...) seront
ajoutés dans les sprints suivants.
"""

import argparse
import datetime
import logging
import time
from pathlib import Path

import audio_io
import config
import gpio_io

logger = logging.getLogger(__name__)

STATE_ATTENTE = "attente"
STATE_DECROCHE = "decroche"
STATE_NUMEROTATION = "numerotation"
STATE_LECTURE_MESSAGE = "lecture_message"
STATE_ENREGISTREMENT = "enregistrement"

MAIN_LOOP_POLL_SEC = 0.05


def message_path_for_digit(digit: int) -> Path:
    """Fichier associé à un chiffre composé, avec repli sur le message générique (§1.2)."""
    path = config.message_wav(digit)
    if path.exists():
        return path
    logger.info("Aucun message attribué au chiffre %d, message générique utilisé.", digit)
    return config.MESSAGE_GENERIQUE_WAV


def timestamped_recording_path(now: datetime.datetime = None) -> Path:
    """Chemin horodaté pour un nouvel enregistrement, jamais en écrasant un fichier existant (§7.2)."""
    now = now or datetime.datetime.now()
    base = now.strftime("message_%Y-%m-%d_%H-%M-%S")
    path = config.MESSAGES_DIR / f"{base}.wav"
    suffix = 1
    while path.exists():
        path = config.MESSAGES_DIR / f"{base}_{suffix}.wav"
        suffix += 1
    return path


class GuestBookStateMachine:
    """Boucle des états du parcours invité nominal (§1.2, §5.1)."""

    def __init__(self, inputs: gpio_io.PhoneInputs) -> None:
        self.inputs = inputs
        self.state = STATE_ATTENTE

    def run_forever(self) -> None:
        logger.info("Machine à états démarrée, état initial : %s", self.state)
        while True:
            self._run_attente()

    def _run_attente(self) -> None:
        self.state = STATE_ATTENTE
        while not self.inputs.is_hook_up():
            time.sleep(MAIN_LOOP_POLL_SEC)
        self._run_decroche()

    def _run_decroche(self) -> None:
        self.state = STATE_DECROCHE
        logger.info("Décroché : tonalité")
        self.inputs.reset_dial()
        audio_io.play(
            config.TONALITE_WAV,
            should_continue=lambda: self.inputs.is_hook_up() and not self.inputs.has_pulses(),
        )
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la tonalité, retour en attente.")
            return
        # La tonalité s'est arrêtée dès la première impulsion détectée (§1.2).
        self._run_numerotation()

    def _run_numerotation(self) -> None:
        self.state = STATE_NUMEROTATION
        logger.info("Numérotation en cours")
        digit = None
        while self.inputs.is_hook_up():
            digit = self.inputs.pop_digit()
            if digit is not None:
                break
            time.sleep(MAIN_LOOP_POLL_SEC)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la numérotation, retour en attente.")
            return
        logger.info("Chiffre composé : %d", digit)
        self._run_lecture_message(digit)

    def _run_lecture_message(self, digit: int) -> None:
        self.state = STATE_LECTURE_MESSAGE
        message_path = message_path_for_digit(digit)
        logger.info("Lecture du message : %s", message_path.name)
        audio_io.play(message_path, should_continue=self.inputs.is_hook_up)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le message, retour en attente.")
            return

        audio_io.play(config.BIP_WAV, should_continue=self.inputs.is_hook_up)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le bip, retour en attente.")
            return

        self._run_enregistrement()

    def _run_enregistrement(self) -> None:
        self.state = STATE_ENREGISTREMENT
        path = timestamped_recording_path()
        logger.info("Début de l'enregistrement : %s", path.name)
        result = audio_io.record(
            path, max_duration_sec=config.MAX_RECORD_SEC,
            should_continue=self.inputs.is_hook_up,
        )
        logger.info("Fin de l'enregistrement (%s) : %s", result, path.name)
        # Retour à l'attente : la boucle run_forever() relance _run_attente().


def run_test_mode() -> None:
    """Affiche en direct l'état des 3 GPIO, pour valider câblage et sens logiques (§7.6, point 3)."""
    if gpio_io.GPIO is None:
        raise SystemExit("RPi.GPIO indisponible : le mode --test doit être exécuté sur le Raspberry Pi.")
    GPIO = gpio_io.GPIO
    GPIO.setmode(GPIO.BCM)
    for pin in (config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.DIAL_PULSE_PIN):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    print("Mode test GPIO — décrochez / tournez le cadran pour valider le câblage. Ctrl+C pour quitter.\n")
    print(f"HOOK_PIN={config.HOOK_PIN} (actif={config.HOOK_ACTIVE_STATE}) | "
          f"DIAL_OFFNORMAL_PIN={config.DIAL_OFFNORMAL_PIN} | "
          f"DIAL_PULSE_PIN={config.DIAL_PULSE_PIN} (actif niveau {config.OFFNORMAL_ACTIF_LEVEL})\n")
    try:
        while True:
            hook_raw = GPIO.input(config.HOOK_PIN)
            offnormal_raw = GPIO.input(config.DIAL_OFFNORMAL_PIN)
            pulse_raw = GPIO.input(config.DIAL_PULSE_PIN)
            hook_state = "DECROCHE" if hook_raw == gpio_io.HOOK_ACTIVE_LEVEL else "raccroché"
            offnormal_state = "actif (cadran en mouvement)" if offnormal_raw == gpio_io.DIAL_ACTIVE_LEVEL else "repos"
            pulse_state = "actif" if pulse_raw == gpio_io.DIAL_ACTIVE_LEVEL else "repos"
            print(f"\rcrochet={hook_state:<10} | off-normal={offnormal_state:<25} | impulsion={pulse_state:<8}",
                  end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nArrêt du mode test.")
    finally:
        GPIO.cleanup()


def _check_required_audio_files() -> None:
    required = [config.TONALITE_WAV, config.BIP_WAV, config.MESSAGE_GENERIQUE_WAV]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(
            "Fichiers audio requis manquants : " + ", ".join(missing) +
            " — lancez d'abord `python3 prepare_audio.py`."
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true",
                         help="Mode test GPIO (affichage temps réel des 3 broches, §7.6).")
    args = parser.parse_args()

    config.ensure_directories()

    if args.test:
        run_test_mode()
        return

    _check_required_audio_files()

    inputs = gpio_io.PhoneInputs()
    gpio_io.setup(inputs)
    try:
        GuestBookStateMachine(inputs).run_forever()
    finally:
        gpio_io.cleanup()


if __name__ == "__main__":
    main()
