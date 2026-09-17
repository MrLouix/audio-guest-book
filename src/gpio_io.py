"""Interface GPIO pour le crochet et le cadran rotatif (§3, §5.1, §7.2).

RPi.GPIO gère l'anti-rebond (paramètre bouncetime de add_event_detect) et
déclenche des callbacks qui mettent à jour PhoneInputs de façon thread-safe.
PhoneInputs ne dépend d'aucune bibliothèque matérielle : il peut aussi être
piloté manuellement (mode --test, futurs tests automatisés).
"""

import logging
import threading
from collections import deque
from typing import Any, Dict, Optional

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
PULSE_ACTIVE_LEVEL = _active_level(config.PULSE_ACTIF_LEVEL)


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
            logger.debug(f"dial_active: {was_active} -> {active}, pulse_count={self._pulse_count}")
            if active and not was_active:
                self._pulse_count = 0
                logger.debug("RESET pulse_count to 0")
            elif was_active and not active:
                if self._pulse_count == 0:
                    # Un aller-retour du cadran sans la moindre impulsion n'est
                    # pas un chiffre : c'est un rebond du contact off-normal, ou
                    # le cadran effleuré sans atteindre la butée. Le valider
                    # produisait un « 0 » fantôme (0 % 10) au milieu du numéro,
                    # la salve de rebond du retour au repos en insérant parfois
                    # plusieurs d'affilée.
                    logger.debug("rotation sans impulsion : aucun chiffre validé")
                    return
                digit = self._pulse_count % 10
                self._digit_queue.append(digit)
                logger.debug(f"VALIDATED digit: {digit} (from {self._pulse_count} pulses)")
                self._pulse_count = 0

    def register_pulse(self) -> None:
        with self._lock:
            if self._dial_active:
                self._pulse_count += 1
                logger.debug(f"PULSE detected, pulse_count={self._pulse_count}, dial_active={self._dial_active}")
            else:
                logger.debug(f"PULSE IGNORED (dial not active), pulse_count={self._pulse_count}")

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

    def is_dial_active(self) -> bool:
        """Cadran hors de sa position de repos : un chiffre est en cours de composition.

        Complète has_pulses(), qui n'est vrai qu'entre la première impulsion et
        le retour au repos : is_dial_active() couvre tout le mouvement du
        cadran, y compris l'instant qui précède la première impulsion. Le mode
        restitution s'en sert pour ne jamais valider un numéro incomplet alors
        que l'utilisateur est en train de composer le chiffre suivant (§5.7).
        """
        with self._lock:
            return self._dial_active

    def pop_digit(self) -> Optional[int]:
        with self._lock:
            return self._digit_queue.popleft() if self._digit_queue else None


def _bouncetime_ms(secondes: float) -> int:
    """Fenêtre d'anti-rebond en millisecondes, telle que RPi.GPIO l'accepte.

    add_event_detect refuse un bouncetime nul ou négatif : un réglage à 0 s
    (anti-rebond désactivé) doit donner 1 ms, pas une exception au démarrage.
    """
    return max(1, int(secondes * 1000))


def setup(inputs: PhoneInputs) -> None:
    """Configure les GPIO en entrée (pull-up) et branche les callbacks matériels sur inputs."""
    if GPIO is None:
        raise RuntimeError("RPi.GPIO indisponible : ce module doit s'exécuter sur un Raspberry Pi.")

    GPIO.setmode(GPIO.BCM)
    for pin in (config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.DIAL_PULSE_PIN):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    inputs.set_hook(GPIO.input(config.HOOK_PIN) == HOOK_ACTIVE_LEVEL)
    inputs.set_dial_active(GPIO.input(config.DIAL_OFFNORMAL_PIN) == DIAL_ACTIVE_LEVEL)

    # Le crochet et l'off-normal sont des *états* : les deux fronts comptent et
    # le callback relit la broche pour connaître le niveau courant.
    def _on_hook(_channel: int) -> None:
        inputs.set_hook(GPIO.input(config.HOOK_PIN) == HOOK_ACTIVE_LEVEL)

    def _on_offnormal(_channel: int) -> None:
        inputs.set_dial_active(GPIO.input(config.DIAL_OFFNORMAL_PIN) == DIAL_ACTIVE_LEVEL)

    # Une impulsion, elle, est un *événement* : on la compte sur son front,
    # sans relire la broche. Relire le niveau dans le callback — qui s'exécute
    # après le front, avec une latence variable — revenait à perdre toute
    # impulsion plus courte que cette latence, et donc à lire un chiffre trop
    # petit dès que le contact est usé (tests/scope_impulsions.py montre la
    # perte, colonne « latence » du balayage).
    def _on_pulse(_channel: int) -> None:
        inputs.register_pulse()

    front_impulsion = GPIO.FALLING if PULSE_ACTIVE_LEVEL == 0 else GPIO.RISING
    GPIO.add_event_detect(config.HOOK_PIN, GPIO.BOTH, callback=_on_hook,
                           bouncetime=_bouncetime_ms(config.HOOK_DEBOUNCE_SEC))
    GPIO.add_event_detect(config.DIAL_OFFNORMAL_PIN, GPIO.BOTH, callback=_on_offnormal,
                           bouncetime=_bouncetime_ms(config.OFFNORMAL_DEBOUNCE_SEC))
    GPIO.add_event_detect(config.DIAL_PULSE_PIN, front_impulsion, callback=_on_pulse,
                           bouncetime=_bouncetime_ms(config.PULSE_DEBOUNCE_SEC))
    logger.info("GPIO initialisés (hook=%s, off-normal=%s actif %s, pulse=%s actif %s)",
                config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.OFFNORMAL_ACTIF_LEVEL,
                config.DIAL_PULSE_PIN, config.PULSE_ACTIF_LEVEL)


def cleanup() -> None:
    if GPIO is not None:
        GPIO.cleanup()


# Instance globale pour stocker l'état des GPIO (si RPi.GPIO est disponible)
_phone_inputs: Optional[PhoneInputs] = None


def get_phone_inputs() -> Optional[PhoneInputs]:
    """Retourne l'instance PhoneInputs si disponible."""
    return _phone_inputs


def set_phone_inputs(inputs: PhoneInputs) -> None:
    """Définit l'instance PhoneInputs à utiliser pour get_current_status."""
    global _phone_inputs
    _phone_inputs = inputs


def get_current_status() -> Optional[Dict[str, Any]]:
    """Retourne l'état actuel des GPIO si disponible.
    
    Retourne un dictionnaire avec :
    {
        "hook": "DECROCHE" ou "raccroché",
        "dial_offnormal": "actif" ou "repos",
        "dial_pulse": "actif" ou "repos" (ou "inconnu" si pas de pulse détecté)
    }
    
    Retourne None si RPi.GPIO n'est pas disponible (mode démo).
    """
    import config
    
    # Si PhoneInputs est disponible et a été initialisé
    if _phone_inputs is not None:
        hook_active = _phone_inputs.is_hook_up()
        # Accesseur public plutôt que l'attribut privé : is_dial_active() prend
        # le verrou de PhoneInputs, ce qui importe ici car cette fonction est
        # appelée depuis le thread Flask alors que _dial_active est muté par les
        # callbacks GPIO.
        dial_active = _phone_inputs.is_dial_active()
        
        # Déterminer les états
        hook_state = "DECROCHE" if hook_active else "raccroché"
        dial_offnormal_state = "actif" if dial_active else "repos"
        
        return {
            "hook": hook_state,
            "dial_offnormal": dial_offnormal_state,
            "dial_pulse": "repos",  # On ne peut pas lire directement l'état de la pulse
        }
    
    # Si RPi.GPIO est disponible mais PhoneInputs n'a pas été initialisé
    if GPIO is not None:
        try:
            # Lire directement les valeurs des pins
            hook_value = GPIO.input(config.HOOK_PIN)
            dial_offnormal_value = GPIO.input(config.DIAL_OFFNORMAL_PIN)
            dial_pulse_value = GPIO.input(config.DIAL_PULSE_PIN)
            
            hook_active = (hook_value == HOOK_ACTIVE_LEVEL)
            dial_active = (dial_offnormal_value == DIAL_ACTIVE_LEVEL)
            pulse_active = (dial_pulse_value == PULSE_ACTIVE_LEVEL)
            
            return {
                "hook": "DECROCHE" if hook_active else "raccroché",
                "dial_offnormal": "actif" if dial_active else "repos",
                "dial_pulse": "actif" if pulse_active else "repos",
            }
        except Exception:
            # Erreur lors de la lecture GPIO
            return None
    
    # RPi.GPIO non disponible (mode développement/démo)
    return None


def is_gpio_available() -> bool:
    """Indique si RPi.GPIO est disponible."""
    return GPIO is not None
