"""Interface GPIO pour le crochet et le cadran rotatif (§3, §5.1, §7.2).

Les trois contacts sont lus par **échantillonnage** : un thread relit les
broches à GPIO_ECHANTILLONNAGE_HZ et ne retient un changement d'état que s'il
se maintient assez longtemps (FiltreContact). C'est ce qui permet de lire un
cadran dont le contact d'impulsions est usé : un tel contact grésille pendant
toute la fermeture — jusqu'à 40 fronts en 25 ms — alors que le repos entre
deux impulsions, lui, reste franc. Le bouncetime de RPi.GPIO ne savait pas
exprimer cette dissymétrie : sa fenêtre unique bloque tous les fronts pendant
N ms après un front accepté, ce qui ne peut pas à la fois absorber le
grésillement et respecter des impulsions dont la durée varie du simple au
triple (32 à 89 ms mesurés).

PhoneInputs ne dépend d'aucune bibliothèque matérielle : il peut aussi être
piloté manuellement (mode --test, tests automatisés). FiltreContact non plus,
ce qui permet à tests/scope_impulsions.py de rejouer une capture réelle dans
exactement le filtre du service.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

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


class FiltreContact:
    """Filtre d'intégration à hystérésis dissymétrique, sans dépendance matérielle.

    Alimenté par des échantillons (instant, actif), il ne fait basculer son
    état stable que lorsque le niveau brut s'est maintenu plus longtemps que
    le seuil correspondant à son sens. Deux seuils, donc, et c'est tout
    l'intérêt : sur un contact d'impulsions usé, on ouvre l'impulsion vite
    (`confirm_actif`, quelques millisecondes) mais on ne la clôt qu'après un
    repos franc (`confirm_repos`, quelques dizaines), assez long pour ignorer
    les micro-coupures du grésillement et assez court pour ne jamais souder
    deux impulsions voisines.

    Un seuil de confirmation symétrique (les deux valeurs égales) redonne le
    comportement attendu d'un contact sain : c'est ainsi que sont filtrés le
    crochet et le contact off-normal.
    """

    def __init__(self, confirm_actif: float, confirm_repos: float,
                 actif: bool = False) -> None:
        self.confirm_actif = confirm_actif
        self.confirm_repos = confirm_repos
        self._stable = actif
        self._candidat = actif
        self._depuis = 0.0
        self._debut_etat = 0.0

    @property
    def etat(self) -> bool:
        return self._stable

    @property
    def debut_etat(self) -> float:
        """Instant où le niveau de l'état stable courant est réellement apparu.

        La bascule, elle, est prononcée un délai de confirmation plus tard. Le
        service agit à la bascule — il ne peut pas faire autrement, il ne
        connaît pas l'avenir — mais quiconque *reconstruit* le signal a besoin
        de l'instant vrai, faute de quoi toutes les durées mesurées sont
        décalées de la différence entre les deux confirmations.
        """
        return self._debut_etat

    def echantillon(self, instant: float, actif: bool) -> Optional[bool]:
        """Nouvel état stable s'il vient de changer à cet instant, sinon None."""
        if actif != self._candidat:
            # Le niveau brut vient de bouger : le compte à rebours repart.
            self._candidat = actif
            self._depuis = instant
            return None
        if actif == self._stable:
            return None
        seuil = self.confirm_actif if actif else self.confirm_repos
        if instant - self._depuis >= seuil:
            self._stable = actif
            self._debut_etat = self._depuis
            return actif
        return None


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
            logger.debug("cadran %s -> %s (compteur : %d impulsion(s))",
                         "actif" if was_active else "repos",
                         "actif" if active else "repos", self._pulse_count)
            if active and not was_active:
                self._pulse_count = 0
                logger.debug("début de rotation : compteur remis à zéro")
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
                logger.debug("chiffre validé : %d (%d impulsion(s) comptée(s))",
                             digit, self._pulse_count)
                self._pulse_count = 0

    def register_pulse(self) -> None:
        with self._lock:
            if self._dial_active:
                self._pulse_count += 1
                logger.debug("impulsion comptée (%d depuis le début de la rotation)",
                             self._pulse_count)
            else:
                # Cadran au repos : impulsion parasite (contact qui grésille,
                # cadran effleuré). Comptée nulle part, mais visible ici — c'est
                # la trace qui explique un chiffre perdu ou un « 0 » fantôme.
                logger.debug("impulsion ignorée : le cadran est au repos")

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

    def has_digits(self) -> bool:
        """Un chiffre validé attend-il d'être consommé ? (sans le consommer)

        Complète has_pulses(), qui retombe à faux dès que le cadran revient au
        repos : les deux réunis disent « quelque chose a été composé depuis le
        décroché », ce dont la tonalité a besoin pour ne pas repartir entre
        deux chiffres (§1.2).
        """
        with self._lock:
            return bool(self._digit_queue)

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


class _Contact:
    """Une broche, son filtre, et ce qu'on fait de son état une fois stable."""

    def __init__(self, nom: str, pin: int, niveau_actif: int,
                 filtre: FiltreContact) -> None:
        self.nom = nom
        self.pin = pin
        self.niveau_actif = niveau_actif
        self.filtre = filtre


def contacts_configures(etats_initiaux: Dict[str, bool]) -> List[_Contact]:
    """Les trois contacts et leurs filtres, réglés par config.py.

    Séparé de setup() pour que tests/scope_impulsions.py puisse construire les
    mêmes filtres et rejouer une capture dans exactement ce que fait le service.
    """
    return [
        _Contact("crochet", config.HOOK_PIN, HOOK_ACTIVE_LEVEL,
                 FiltreContact(config.HOOK_CONFIRM_SEC, config.HOOK_CONFIRM_SEC,
                               etats_initiaux.get("crochet", False))),
        _Contact("off-normal", config.DIAL_OFFNORMAL_PIN, DIAL_ACTIVE_LEVEL,
                 FiltreContact(config.OFFNORMAL_CONFIRM_SEC, config.OFFNORMAL_CONFIRM_SEC,
                               etats_initiaux.get("off-normal", False))),
        _Contact("impulsions", config.DIAL_PULSE_PIN, PULSE_ACTIVE_LEVEL,
                 FiltreContact(config.PULSE_MIN_ACTIF_SEC, config.PULSE_MIN_REPOS_SEC,
                               etats_initiaux.get("impulsions", False))),
    ]


def appliquer(inputs: PhoneInputs, nom: str, actif: bool) -> None:
    """Répercute sur PhoneInputs le changement d'état stable d'un contact.

    Le crochet et l'off-normal sont des états, recopiés tels quels ;
    l'impulsion est un événement, comptée au passage à l'actif et ignorée au
    retour au repos.
    """
    if nom == "crochet":
        inputs.set_hook(actif)
    elif nom == "off-normal":
        inputs.set_dial_active(actif)
    elif actif:
        inputs.register_pulse()


_arret = threading.Event()
_thread: Optional[threading.Thread] = None


def _boucle_echantillonnage(inputs: PhoneInputs, contacts: List[_Contact],
                            periode_rapide: float, periode_repos: float,
                            activite_sec: float) -> None:
    """Relit les broches et alimente les filtres jusqu'à l'arrêt du service.

    La cadence suit l'activité : rapide pendant `activite_sec` après le dernier
    changement de niveau brut, au repos le reste du temps. Le coût d'un tour de
    boucle étant presque entièrement celui du réveil du thread, c'est la
    cadence — et non le travail fait à chaque tour — qui décide de ce que le
    service consomme les 99 % du temps où personne ne touche au téléphone.
    """
    # À ces cadences, un tour de boucle est fait pour l'essentiel de recherches
    # d'attributs : on les sort toutes d'avance.
    lire = GPIO.input
    attendre = _arret.wait
    arrete = _arret.is_set
    horloge = time.monotonic
    lignes = tuple((c.nom, c.pin, c.niveau_actif, c.filtre.echantillon)
                   for c in contacts)
    # Dernier niveau brut lu par contact, pour repérer un front sans attendre
    # que le filtre se prononce : c'est lui qui relance la cadence rapide.
    bruts: List[Optional[bool]] = [None] * len(lignes)
    # Instant de la dernière bascule confirmée, par contact : sa différence
    # avec la suivante donne la durée de l'état qui vient de finir. C'est la
    # mesure qui règle PULSE_MIN_ACTIF_SEC et PULSE_MIN_REPOS_SEC — largeur
    # d'impulsion et repos entre deux — sans avoir à sortir l'oscilloscope de
    # tests/scope_impulsions.py, service arrêté.
    # Le service démarre en cadence rapide : le téléphone peut très bien être
    # déjà décroché, et la première seconde ne coûte rien.
    demarrage = horloge()
    bascules: List[float] = [demarrage] * len(lignes)
    rapide_jusqu_a = demarrage + activite_sec
    while not arrete():
        maintenant = horloge()
        for rang, (nom, pin, niveau_actif, echantillon) in enumerate(lignes):
            try:
                brut = lire(pin) == niveau_actif
            except RuntimeError:
                # cleanup() a libéré les GPIO pendant qu'on lisait : on sort.
                return
            if brut != bruts[rang]:
                bruts[rang] = brut
                rapide_jusqu_a = maintenant + activite_sec
            stable = echantillon(maintenant, brut)
            if stable is not None:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("%s -> %s (état précédent tenu %.0f ms)", nom,
                                 "actif" if stable else "repos",
                                 (maintenant - bascules[rang]) * 1000)
                bascules[rang] = maintenant
                appliquer(inputs, nom, stable)
        # wait() plutôt que sleep() : l'arrêt est pris en compte tout de suite.
        attendre(periode_rapide if maintenant < rapide_jusqu_a else periode_repos)


def setup(inputs: PhoneInputs) -> None:
    """Configure les GPIO en entrée (pull-up) et démarre leur échantillonnage."""
    if GPIO is None:
        raise RuntimeError("RPi.GPIO indisponible : ce module doit s'exécuter sur un Raspberry Pi.")

    global _thread

    GPIO.setmode(GPIO.BCM)
    for pin in (config.HOOK_PIN, config.DIAL_OFFNORMAL_PIN, config.DIAL_PULSE_PIN):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Les filtres démarrent sur l'état réel des broches, pour qu'un téléphone
    # déjà décroché au lancement du service ne produise pas un faux front.
    etats = {
        "crochet": GPIO.input(config.HOOK_PIN) == HOOK_ACTIVE_LEVEL,
        "off-normal": GPIO.input(config.DIAL_OFFNORMAL_PIN) == DIAL_ACTIVE_LEVEL,
        "impulsions": GPIO.input(config.DIAL_PULSE_PIN) == PULSE_ACTIVE_LEVEL,
    }
    inputs.set_hook(etats["crochet"])
    inputs.set_dial_active(etats["off-normal"])

    rapide = max(config.GPIO_ECHANTILLONNAGE_HZ, 1.0)
    # La cadence de repos ne peut pas dépasser la cadence rapide : l'inverse
    # ferait ralentir la boucle dès qu'il se passe quelque chose.
    repos = min(max(config.GPIO_ECHANTILLONNAGE_REPOS_HZ, 1.0), rapide)
    _arret.clear()
    _thread = threading.Thread(
        target=_boucle_echantillonnage,
        args=(inputs, contacts_configures(etats), 1.0 / rapide, 1.0 / repos,
              config.GPIO_ACTIVITE_SEC),
        name="gpio-echantillonnage", daemon=True)
    _thread.start()

    logger.info("GPIO échantillonnés à %.0f Hz en activité, %.0f Hz au repos "
                "(crochet=%s actif %s, off-normal=%s actif %s, impulsions=%s "
                "actif %s, maintien %.0f/%.0f ms)",
                rapide, repos, config.HOOK_PIN, config.HOOK_ACTIVE_STATE,
                config.DIAL_OFFNORMAL_PIN, config.OFFNORMAL_ACTIF_LEVEL,
                config.DIAL_PULSE_PIN, config.PULSE_ACTIF_LEVEL,
                config.PULSE_MIN_ACTIF_SEC * 1000, config.PULSE_MIN_REPOS_SEC * 1000)


def cleanup() -> None:
    """Arrête l'échantillonnage puis libère les broches, dans cet ordre.

    L'inverse laisserait la boucle lire une broche déjà libérée.
    """
    global _thread
    _arret.set()
    if _thread is not None:
        _thread.join(timeout=1.0)
        _thread = None
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
