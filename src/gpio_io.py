"""Interface GPIO pour le crochet et le cadran rotatif (§3, §5.1, §7.2).

RPi.GPIO gère l'anti-rebond (paramètre bouncetime de add_event_detect) et
déclenche des callbacks qui mettent à jour PhoneInputs de façon thread-safe.
PhoneInputs ne dépend d'aucune bibliothèque matérielle : il peut aussi être
piloté manuellement (mode --test, futurs tests automatisés).
"""

import logging
import threading
from collections import deque
from typing import Optional

import config

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    GPIO = None

_LEVELS = {"LOW": 0, "HIGH": 1}


def _active_level(name: str) -> int:
    try:
        return _LEVELS[name.upper()]
    except KeyError:
        raise ValueError(f"Niveau logique inconnu : {name!r} (attendu LOW ou HIGH)") from None


HOOK_ACTIVE_LEVEL = _active_level(config.HOOK_ACTIVE_STATE)
DIAL_ACTIVE_LEVEL = _active_level(config.OFFNORMAL_ACTIF_LEVEL)


class PhoneInputs:
    """État partagé, thread-safe, des 3 contacts crochet/off-normal/impulsions.

    Le comptage des impulsions n'est actif que pendant que le cadran est hors
    de sa position de repos (off-normal actif) ; le chiffre est validé (mis
    en file) au retour du cadran au repos, 10 impulsions valant le chiffre 0
    (§1.2, §5.1).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hook_active = False
        self._dial_active = False
        self._pulse_count = 0
        self._digit_queue: "deque[int]" = deque()

    def set_hook(self, active: bool) -> None:
        with self._lock:
            self._hook_active = active

    def set_dial_active(self, active: bool) -> None:
        with self._lock:
            was_active = self._dial_active
            self._dial_active = active
            if active and not was_active:
                self._pulse_count = 0
            elif was_active and not active:
                self._digit_queue.append(self._pulse_count % 10)
                self._pulse_count = 0

    def register_pulse(self) -> None:
        with self._lock:
            if self._dial_active:
                self._pulse_count += 1

    def reset_dial(self) -> None:
        """Purge tout comptage/chiffre en attente (ex. avant une nouvelle tonalité)."""
        with self._lock:
            self._dial_active = False
            self._pulse_count = 0
            self._digit_queue.clear()

    def is_hook_up(self) -> bool:
        with self._lock:
            return self._hook_active

    def has_pulses(self) -> bool:
        with self._lock:
            return self._pulse_count > 0

    def pop_digit(self) -> Optional[int]:
        with self._lock:
            return self._digit_queue.popleft() if self._digit_queue else None


def setup(inputs: PhoneInputs) -> None:
    """Configure les GPIO en entrée (pull-up) et branche les callbacks matériels sur inputs."""
    if GPIO is None:
        raise RuntimeError("RPi.GPIO indisponible : ce module doit s'exécuter sur un Raspberry Pi.")

    GPIO.setmode(GPIO.BCM)
    for pin in (config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.DIAL_PULSE_PIN):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    inputs.set_hook(GPIO.input(config.HOOK_PIN) == HOOK_ACTIVE_LEVEL)
    inputs.set_dial_active(GPIO.input(config.DIAL_OFFNORMAL_PIN) == DIAL_ACTIVE_LEVEL)

    def _on_hook(_channel: int) -> None:
        inputs.set_hook(GPIO.input(config.HOOK_PIN) == HOOK_ACTIVE_LEVEL)

    def _on_offnormal(_channel: int) -> None:
        inputs.set_dial_active(GPIO.input(config.DIAL_OFFNORMAL_PIN) == DIAL_ACTIVE_LEVEL)

    def _on_pulse(_channel: int) -> None:
        if GPIO.input(config.DIAL_PULSE_PIN) == DIAL_ACTIVE_LEVEL:
            inputs.register_pulse()

    GPIO.add_event_detect(config.HOOK_PIN, GPIO.BOTH, callback=_on_hook,
                           bouncetime=int(config.HOOK_DEBOUNCE_SEC * 1000))
    GPIO.add_event_detect(config.DIAL_OFFNORMAL_PIN, GPIO.BOTH, callback=_on_offnormal,
                           bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000))
    GPIO.add_event_detect(config.DIAL_PULSE_PIN, GPIO.BOTH, callback=_on_pulse,
                           bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000))
    logger.info("GPIO initialisés (hook=%s, off-normal=%s, pulse=%s)",
                config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.DIAL_PULSE_PIN)


def cleanup() -> None:
    if GPIO is not None:
        GPIO.cleanup()
