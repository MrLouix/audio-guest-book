"""Script principal du livre d'or téléphonique — machine à états (§5.1).

Sprint 2 : scénario nominal (appel sortant) — attente → décroché (tonalité)
→ numérotation → lecture du message → enregistrement → retour à l'attente.

Sprint 3 : sonnerie périodique + déclenchement à distance (ring_trigger), et
scénario « appel entrant » (§1.2) — un décroché pendant la sonnerie ou dans
la fenêtre de grâce qui suit simule un vrai appel : message tiré au hasard,
sans tonalité ni cadran.

Sprint 4 : surcouches de fiabilité (§7.1, §7.2, §7.3) — gestion d'exception
globale, attente active de la carte son au démarrage, refus de démarrer sans
les fichiers audio requis, écriture atomique et continue de status.json,
contrôle d'espace disque avant enregistrement, tolérance aux micro-coupures
du crochet pendant l'enregistrement, conservation des enregistrements très
courts, et rotation des logs applicatifs.

Sprint 14 : tonalité fidèle à un poste à cadran du réseau français (§1.2) —
un 440 Hz continu, tenu tant que rien n'est composé et coupé net à la première
impulsion (et non au chiffre complet), porté par `Tonalite`, dans les deux
modes.

Sprint 13 : mode restitution (§5.7) — après l'événement, le téléphone devient
un lecteur des messages laissés par les invités. Décroché → tonalité →
numéro de RESTITUTION_DIGITS_MAX chiffres au plus composé au cadran → lecture
du message correspondant, les enregistrements étant numérotés 1..N dans
l'ordre chronologique. Ni sonnerie, ni message des mariés, ni bip, ni
enregistrement : le mode est en lecture seule sur messages/ — la tonalité,
elle, est la même qu'en mode mariage.
"""

import argparse
import datetime
import logging
import random
import re
import shutil
import threading
import time
import wave
from pathlib import Path
from typing import Callable, List, Optional

import alsa_io
import audio_io
import config
import fichiers
import gpio_io
import mode_io
import status_io
import traitement_audio

logger = logging.getLogger(__name__)

# Les états publiés dans status.json ; définis par status_io, qui porte le
# contrat du fichier partagé avec le dashboard et le watchdog.
STATE_ATTENTE = status_io.ETAT_ATTENTE
STATE_SONNERIE = status_io.ETAT_SONNERIE
STATE_DECROCHE = status_io.ETAT_DECROCHE
STATE_NUMEROTATION = status_io.ETAT_NUMEROTATION
STATE_LECTURE_MESSAGE = status_io.ETAT_LECTURE_MESSAGE
STATE_APPEL_REPONDU = status_io.ETAT_APPEL_REPONDU
STATE_ENREGISTREMENT = status_io.ETAT_ENREGISTREMENT
STATE_RESTITUTION_NUMEROTATION = status_io.ETAT_RESTITUTION_NUMEROTATION
STATE_RESTITUTION_LECTURE = status_io.ETAT_RESTITUTION_LECTURE
STATE_ERREUR = status_io.ETAT_ERREUR

MAIN_LOOP_POLL_SEC = 0.05

# Scrutation pendant la tonalité : elle doit tomber *avec* la première
# impulsion, pas un dixième de seconde après. Le coût est nul — un décroché
# sans numérotation dure quelques secondes.
TONALITE_POLL_SEC = 0.02

# Scrutation du raccroché pendant l'enregistrement : c'est elle qui décide de
# la fin du message, donc de ce qui est coupé.
RECORD_POLL_SEC = 0.02


def jouer_combine(path: Path, should_continue: Callable[[], bool], **options) -> str:
    """Joue `path` dans le combiné et l'écouteur secondaire, à leur volume (§4.1).

    Toutes les lectures du parcours sauf la sonnerie passent par ici : sortie
    AUDIO_OUTPUT_COMBINE, volume VOLUME_COMBINE et garde-fou
    AUDIO_PLAY_TIMEOUT_SEC sont relus à chaque appel (réglables à chaud).
    `options` complète l'appel (device, poll_interval).
    """
    return audio_io.play(path, should_continue=should_continue,
                         timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
                         output=config.AUDIO_OUTPUT_COMBINE,
                         volume=config.VOLUME_COMBINE,
                         **options)


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


RECORDING_NAME_RE = re.compile(r"^message_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_(\d+))?$")


def _recording_sort_key(path: Path) -> tuple:
    """Clé de tri chronologique d'un enregistrement d'invité (§5.7).

    L'horodatage inscrit dans le nom du fichier par timestamped_recording_path()
    est la chronologie de référence : il voyage avec le fichier, contrairement à
    la date de modification, qu'une copie rclone ou une restauration de
    sauvegarde peut écraser. Le suffixe de collision _k désigne un
    enregistrement postérieur dans la même seconde, et se compare comme un
    entier (_2 avant _10). Repli sur la mtime pour un fichier au nom non
    conforme, ajouté à la main dans messages/.
    """
    match = RECORDING_NAME_RE.match(path.stem)
    if match:
        try:
            stamp = datetime.datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            stamp = None
        if stamp is not None:
            return (stamp.timestamp(), int(match.group(2) or 0), path.name)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, 0, path.name)


def recorded_messages() -> List[Path]:
    """Messages des invités, numérotés 1..N dans l'ordre chronologique (§5.7).

    Lecture seule : ce mode ne crée, ne modifie et ne supprime jamais rien
    dans messages/.
    """
    # Dossier absent ou illisible : aucun message, jamais d'exception (§7.4).
    return sorted(fichiers.fichiers_wav(config.MESSAGES_DIR), key=_recording_sort_key)


def restitution_absence_wav() -> Path:
    """Annonce « aucun message disponible » ; repli sur le bip si elle n'a pas été fournie (§5.7)."""
    if config.AUCUN_MESSAGE_WAV.exists():
        return config.AUCUN_MESSAGE_WAV
    return config.BIP_WAV


def available_random_messages() -> List[Path]:
    """Tous les messages des mariés pouvant être tirés au hasard (§1.2) : message_N.wav + message_generique.wav."""
    candidates = [p for p in map(config.message_wav, range(10)) if p.exists()]
    if config.MESSAGE_GENERIQUE_WAV.exists():
        candidates.append(config.MESSAGE_GENERIQUE_WAV)
    return candidates


# Un ring_trigger non supprimable n'est signalé qu'une fois : la boucle
# d'attente le retente à 20 Hz.
_ring_trigger_erreur_signalee = False


def consume_ring_trigger() -> bool:
    """Consomme le fichier drapeau ring_trigger s'il existe (déclenchement à distance, §5.1, §8).

    La suppression fait office de test : un seul appel système, et une
    suppression concurrente (par un autre processus) ne lève pas d'erreur.
    """
    global _ring_trigger_erreur_signalee
    try:
        config.RING_TRIGGER_FILE.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        # Fichier présent mais non supprimable (droits) : le considérer
        # consommé ferait sonner en boucle. Il est ignoré, et signalé une fois.
        if not _ring_trigger_erreur_signalee:
            _ring_trigger_erreur_signalee = True
            logger.error("Impossible de consommer %s : %s", config.RING_TRIGGER_FILE, exc)
        return False
    _ring_trigger_erreur_signalee = False
    return True


def disk_space_state(path: Path) -> str:
    """Renvoie 'ok', 'alerte' ou 'critique' selon les seuils de stockage (§7.3)."""
    try:
        free_mb = shutil.disk_usage(path).free / (1024 * 1024)
    except OSError:
        logger.exception("Impossible de lire l'espace disque disponible pour %s", path)
        return "ok"
    if free_mb < config.DISK_CRITICAL_MB:
        return "critique"
    if free_mb < config.DISK_WARNING_MB:
        return "alerte"
    return "ok"


def wav_duration_sec(path: Path) -> float:
    """Durée d'un fichier WAV en secondes ; 0.0 si illisible (fichier tronqué, §7.2)."""
    try:
        with wave.open(str(path), "rb") as wav_file:
            return wav_file.getnframes() / float(wav_file.getframerate())
    except (wave.Error, OSError, EOFError, ZeroDivisionError):
        return 0.0


class HangupConfirmer:
    """Filtre les micro-coupures du crochet (< RECORDING_HANGUP_CONFIRM_SEC) pendant l'enregistrement (§7.2).

    Le raccroché n'est confirmé que si le crochet reste relevé en continu
    pendant au moins confirm_sec ; un faux contact bref est ignoré et
    l'enregistrement se poursuit sans être tronqué.
    """

    def __init__(self, inputs: gpio_io.PhoneInputs, confirm_sec: float = None) -> None:
        self.inputs = inputs
        self.confirm_sec = config.RECORDING_HANGUP_CONFIRM_SEC if confirm_sec is None else confirm_sec
        self._down_since: Optional[float] = None

    def should_continue(self) -> bool:
        if self.inputs.is_hook_up():
            self._down_since = None
            return True
        now = time.monotonic()
        if self._down_since is None:
            self._down_since = now
        return (now - self._down_since) < self.confirm_sec


class Tonalite:
    """Tonalité d'invitation à numéroter, tenue jusqu'à la 1re impulsion (§1.2).

    Sur un poste à cadran du réseau français, c'était le seul son de la ligne
    avant la communication : un 440 Hz continu et non modulé, présent dès le
    décroché et tenu tant que rien n'était composé. La numérotation décimale,
    elle, n'émet aucun signal audio — elle ouvre et referme la boucle N fois
    pour le chiffre N —, et la tonalité tombait net dès la première coupure,
    pas au chiffre complet.

    Le fichier, lui, a une fin : `tenir()` est donc appelée à chaque tour de
    la boucle de numérotation et le rejoue tant que rien n'est parti.
    TONALITE_MAX_SEC borne le total, pour ne pas enchaîner les sous-processus
    sans fin sur un combiné simplement posé à côté du téléphone.
    """

    def __init__(self, inputs: gpio_io.PhoneInputs,
                 device: Optional[str] = None) -> None:
        self.inputs = inputs
        self.device = device
        self._composition_commencee = False
        self._fin = time.monotonic() + max(config.TONALITE_MAX_SEC, 0)
        self._silence_signale = False

    def composition_commencee(self) -> bool:
        """Drapeau collant : vrai dès la première impulsion, jusqu'au raccroché.

        Collant parce que le compteur d'impulsions du chiffre en cours retombe
        à zéro dès que le cadran revient au repos : sans ça, la tonalité
        repartirait entre deux chiffres d'un numéro de restitution.
        """
        if not self._composition_commencee and (self.inputs.has_pulses()
                                                or self.inputs.has_digits()):
            self._composition_commencee = True
        return self._composition_commencee

    def tenir(self) -> None:
        """Rejoue le fichier de tonalité, jusqu'à la première impulsion.

        Bloquant le temps d'une lecture, mais interruptible : `should_continue`
        coupe à la première impulsion comme au raccroché. Rien à faire si le
        budget de TONALITE_MAX_SEC est épuisé — la ligne devient alors
        silencieuse, la numérotation reste possible.
        """
        if self.composition_commencee() or not self.inputs.is_hook_up():
            return
        if time.monotonic() >= self._fin:
            # Sans cette trace, un combiné décroché depuis trois minutes
            # devient muet sans que rien ne dise pourquoi — et le silence
            # ressemble à une panne de carte son.
            if not self._silence_signale:
                self._silence_signale = True
                logger.info("Tonalité arrêtée après %d s sans numérotation "
                            "(TONALITE_MAX_SEC) : ligne silencieuse jusqu'au raccroché.",
                            config.TONALITE_MAX_SEC)
            return

        def continuer() -> bool:
            return self.inputs.is_hook_up() and not self.composition_commencee()

        resultat = jouer_combine(config.TONALITE_WAV, continuer,
                                 poll_interval=TONALITE_POLL_SEC, device=self.device)
        if resultat == "error":
            # Fichier manquant ou aplay en échec : ne pas boucler sur l'erreur,
            # le reste du parcours doit rester praticable (§7.4).
            logger.warning("Tonalité non jouée : la ligne reste silencieuse "
                           "jusqu'à la numérotation.")
            self._fin = 0.0


class GuestBookStateMachine:
    """Boucle des états du parcours invité, nominal et « appel entrant » (§1.2, §5.1)."""

    def __init__(self, inputs: gpio_io.PhoneInputs) -> None:
        self.inputs = inputs
        self.state = STATE_ATTENTE
        self._ring_active = False
        self._last_ring_end_ts: Optional[float] = None
        self._last_ring_start_ts = time.monotonic()
        self._last_random_message: Optional[Path] = None
        self._stop = threading.Event()

    def request_stop(self) -> None:
        """Demande l'arrêt de la boucle au prochain tour d'attente.

        Le service est normalement arrêté par systemd, mais un arrêt coopératif
        évite de laisser des machines à états vivantes dans les harnais de test
        (restitution_test.py, load_test.py), où elles continueraient d'écrire
        status.json et de jouer des sonneries après la fin d'un scénario.
        """
        self._stop.set()

    def _set_state(self, state: str, detail: Optional[str] = None) -> None:
        self.state = state
        status_io.write_status(state, detail)

    @staticmethod
    def _battement(dernier: float, state: str,
                   detail: Callable[[], Optional[str]]) -> float:
        """Republie status.json si STATUS_HEARTBEAT_SEC est écoulé ; retourne l'instant du dernier battement.

        Indispensable dans toute attente sans limite de durée : sans lui, le
        watchdog (§7.1) verrait status.json périmé au bout de
        WATCHDOG_STALE_AFTER_SEC et redémarrerait le service en boucle.
        """
        maintenant = time.monotonic()
        if (maintenant - dernier) < config.STATUS_HEARTBEAT_SEC:
            return dernier
        status_io.write_status(state, detail=detail())
        return maintenant

    def run_forever(self) -> None:
        logger.info("Machine à états démarrée, état initial : %s", self.state)
        while not self._stop.is_set():
            self._run_attente()

    def _is_recent_ring(self) -> bool:
        """Drapeau « sonnerie récente » (§5.1) : actif pendant la sonnerie et RING_ANSWER_GRACE_SEC après."""
        if self._ring_active:
            return True
        if self._last_ring_end_ts is None:
            return False
        return (time.monotonic() - self._last_ring_end_ts) <= config.RING_ANSWER_GRACE_SEC

    def _attente_detail(self) -> Optional[str]:
        """Détail écrit dans status.json en attente : rend le mode courant visible (§5.2)."""
        if mode_io.is_restitution():
            return "mode restitution"
        # Sans ça, une sonnerie coupée volontairement serait indiscernable
        # d'une sonnerie en panne sur le dashboard.
        if config.RING_INTERVAL_SEC <= 0:
            return "sonnerie périodique désactivée"
        return None

    def _run_attente(self) -> None:
        self._set_state(STATE_ATTENTE, detail=self._attente_detail())
        self._last_ring_start_ts = time.monotonic()
        last_heartbeat = time.monotonic()
        while not self._stop.is_set():
            # Paramètres modifiables à chaud depuis /settings (volumes,
            # cadence de la sonnerie…) : relus ici, entre deux communications,
            # jamais au milieu d'une. La relecture est limitée à une par
            # seconde par config.LIVE_RELOAD_SEC.
            changes = config.refresh_live_params()
            if changes:
                logger.info("Paramètres rechargés à chaud : %s",
                            ", ".join(f"{k}={v}" for k, v in sorted(changes.items())))
            # Mode relu à chaque tour : une bascule depuis le dashboard est
            # prise en compte en moins d'une seconde, et jamais au milieu
            # d'une communication déjà engagée (§5.7).
            restitution = mode_io.is_restitution()
            if self.inputs.is_hook_up():
                if restitution:
                    self._run_restitution_decroche()
                elif self._is_recent_ring():
                    self._run_appel_repondu()
                else:
                    self._run_decroche()
                return
            if restitution:
                # Sonnerie neutralisée en restitution (§5.7). Le drapeau
                # ring_trigger est quand même consommé : laissé en place, il
                # déclencherait une sonnerie surprise au retour en mode
                # mariage. L'intervalle de sonnerie est réarmé en continu, pour
                # ne pas sonner immédiatement au moment de la bascule inverse.
                if consume_ring_trigger():
                    logger.info("Sonnerie demandée à distance, ignorée (mode restitution).")
                self._last_ring_start_ts = time.monotonic()
            else:
                sonnerie_demandee = consume_ring_trigger()
                if sonnerie_demandee:
                    logger.info("Sonnerie déclenchée à distance (ring_trigger)")
                # RING_INTERVAL_SEC <= 0 coupe la sonnerie périodique, sans
                # rien changer au reste du parcours (§5.1). Le déclenchement
                # depuis le dashboard, lui, reste toujours actif : c'est un
                # « or », et il ne consulte pas l'intervalle.
                echeance_atteinte = (
                    config.RING_INTERVAL_SEC > 0
                    and (time.monotonic() - self._last_ring_start_ts) >= config.RING_INTERVAL_SEC
                )
                if sonnerie_demandee or echeance_atteinte:
                    self._run_sonnerie()
                    # La sonnerie a publié son propre état ; sans cette
                    # republication, status.json resterait sur « sonnerie »
                    # jusqu'au prochain battement (STATUS_HEARTBEAT_SEC), et le
                    # dashboard afficherait « sonnerie » deux minutes durant
                    # alors que le téléphone est déjà revenu au repos (§5.2).
                    # Coût : une écriture de plus par sonnerie, soit une toutes
                    # les RING_INTERVAL_SEC — le même ordre de grandeur que
                    # l'écriture déjà faite pour entrer en état sonnerie.
                    self._set_state(STATE_ATTENTE, detail=self._attente_detail())
                    last_heartbeat = time.monotonic()
                    continue
            last_heartbeat = self._battement(last_heartbeat, STATE_ATTENTE,
                                             self._attente_detail)
            time.sleep(MAIN_LOOP_POLL_SEC)

    def _sonnerie_continue(self) -> bool:
        return not self.inputs.is_hook_up() and not self._stop.is_set()

    def _run_sonnerie(self) -> None:
        """Rejoue ring_out.wav RING_COUNT fois, RING_PAUSE_SEC de silence entre deux.

        Un décroché coupe la boucle à tout moment, silences compris : le
        drapeau « sonnerie récente » reste actif sur toute la durée, et la
        boucle d'attente enchaîne alors sur l'appel répondu (§1.2).
        """
        repetitions = max(config.RING_COUNT, 1)
        pause = max(config.RING_PAUSE_SEC, 0.0)
        self._set_state(STATE_SONNERIE)
        logger.info("Sonnerie (%d fois, %.1fs de silence entre deux)", repetitions, pause)
        self._ring_active = True
        try:
            for i in range(repetitions):
                resultat = audio_io.play(
                    config.RING_OUT_WAV,
                    should_continue=self._sonnerie_continue,
                    timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
                    output=config.AUDIO_OUTPUT_SONNERIE,
                    volume=config.VOLUME_SONNERIE,
                )
                # Une erreur (fichier absent, aplay en échec) se répéterait à
                # l'identique : inutile d'enchaîner les tentatives.
                if resultat != "completed" or i == repetitions - 1:
                    break
                fin_pause = time.monotonic() + pause
                while time.monotonic() < fin_pause and self._sonnerie_continue():
                    time.sleep(MAIN_LOOP_POLL_SEC)
                if not self._sonnerie_continue():
                    break
        finally:
            self._ring_active = False
            self._last_ring_end_ts = time.monotonic()
            self._last_ring_start_ts = time.monotonic()

    def _pick_random_message(self) -> Path:
        """Tire un message au hasard parmi tous ceux disponibles, sans répéter le précédent (§1.2)."""
        candidates = available_random_messages()
        if not candidates:
            return config.MESSAGE_GENERIQUE_WAV
        choices = [c for c in candidates if c != self._last_random_message] or candidates
        chosen = random.choice(choices)
        self._last_random_message = chosen
        return chosen

    def _hook_up_ignoring_dial(self) -> bool:
        """should_continue pour l'état appel_repondu : le cadran est ignoré, juste logué en debug (§5.1)."""
        digit = self.inputs.pop_digit()
        if digit is not None:
            logger.debug("Impulsion(s) ignorée(s) en appel_repondu (chiffre calculé %d, sans effet).", digit)
        return self.inputs.is_hook_up()

    def _run_appel_repondu(self) -> None:
        self._set_state(STATE_APPEL_REPONDU)
        logger.info("Appel répondu (sonnerie récente) : message aléatoire, sans tonalité ni cadran")
        self.inputs.reset_dial()
        message_path = self._pick_random_message()
        logger.info("Message tiré au hasard : %s", message_path.name)
        self._set_state(STATE_APPEL_REPONDU, detail=message_path.name)
        self._message_bip_enregistrement(message_path, self._hook_up_ignoring_dial,
                                         contexte=" (appel répondu)",
                                         pendant_enregistrement=self._hook_up_ignoring_dial)

    def _decrocher_avec_tonalite(self, contexte: str = "") -> Optional[Tonalite]:
        """Lance la tonalité du décroché (§1.2) ; None si le combiné est raccroché pendant."""
        tonalite = Tonalite(self.inputs)
        tonalite.tenir()
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la tonalité%s, retour en attente.", contexte)
            return None
        return tonalite

    def _run_decroche(self) -> None:
        self._set_state(STATE_DECROCHE)
        logger.info("Décroché : tonalité")
        self.inputs.reset_dial()
        tonalite = self._decrocher_avec_tonalite()
        if tonalite is None:
            return
        digit = self._run_numerotation(tonalite)
        if digit is None:
            return
        logger.info("Chiffre composé : %d", digit)
        self._run_lecture_message(digit)

    def _run_numerotation(self, tonalite: Tonalite) -> Optional[int]:
        """Attend le chiffre composé ; None si le combiné est raccroché avant.

        La tonalité n'est pas finie en entrant ici : elle tient tant que le
        cadran n'a pas envoyé sa première impulsion (§1.2), et c'est
        `tonalite.tenir()` qui la prolonge d'un tour à l'autre.
        """
        self._set_state(STATE_NUMEROTATION)
        logger.info("Numérotation en cours")
        while self.inputs.is_hook_up():
            tonalite.tenir()
            digit = self.inputs.pop_digit()
            if digit is not None:
                return digit
            time.sleep(MAIN_LOOP_POLL_SEC)
        logger.info("Raccroché pendant la numérotation, retour en attente.")
        return None

    def _run_lecture_message(self, digit: int) -> None:
        message_path = message_path_for_digit(digit)
        self._set_state(STATE_LECTURE_MESSAGE, detail=message_path.name)
        logger.info("Lecture du message : %s", message_path.name)
        self._message_bip_enregistrement(message_path, self.inputs.is_hook_up)

    def _message_bip_enregistrement(
            self, message_path: Path, should_continue: Callable[[], bool],
            contexte: str = "",
            pendant_enregistrement: Optional[Callable[[], bool]] = None) -> None:
        """Fin commune des deux parcours du mode mariage : message, bip, enregistrement (§1.2).

        `should_continue` interrompt le message et le bip ;
        `pendant_enregistrement` s'ajoute, pendant l'enregistrement, à la
        tolérance aux micro-coupures du crochet (HangupConfirmer) — il ne la
        remplace pas.
        """
        for quoi, chemin in (("le message", message_path), ("le bip", config.BIP_WAV)):
            jouer_combine(chemin, should_continue)
            if not self.inputs.is_hook_up():
                logger.info("Raccroché pendant %s%s, retour en attente.", quoi, contexte)
                return
        self._run_enregistrement(should_continue=pendant_enregistrement)

    # --- Mode restitution (§5.7) -----------------------------------------

    def _run_restitution_decroche(self) -> None:
        """Décroché en mode restitution : tonalité puis saisie du numéro de message (§5.7).

        Ni message des mariés, ni bip, ni enregistrement : aucun des trois
        parcours de ce mode n'appelle _run_enregistrement().
        """
        self._set_state(STATE_DECROCHE, detail="mode restitution")
        self.inputs.reset_dial()

        # Un seul scan par communication : la numérotation 1..N reste stable
        # du début à la fin de l'appel.
        messages = recorded_messages()
        if not messages:
            logger.warning("Mode restitution : aucun message dans %s.", config.MESSAGES_DIR)
            self._set_state(STATE_RESTITUTION_LECTURE, detail="aucun message disponible")
            jouer_combine(restitution_absence_wav(), self._hook_up_ignoring_dial)
            self._wait_for_hangup()
            return

        logger.info("Décroché (mode restitution) : tonalité, %d message(s) disponible(s)",
                    len(messages))
        # Même tonalité qu'en mode mariage : le mode restitution ne change que
        # ce qu'on entend *après* le numéro (§5.7).
        tonalite = self._decrocher_avec_tonalite(" (mode restitution)")
        if tonalite is None:
            return
        numero = self._run_restitution_numerotation(tonalite)
        if numero is None:
            return
        self._run_restitution_lecture(numero, messages)

    def _run_restitution_numerotation(self, tonalite: Tonalite) -> Optional[int]:
        """Saisie du numéro : RESTITUTION_DIGITS_MAX chiffres au plus (§5.7).

        Le numéro est validé de deux façons : dès le dernier chiffre autorisé —
        aucun chiffre supplémentaire n'est alors accepté — ou après
        RESTITUTION_INTERDIGIT_SEC de silence du cadran (« 1 » puis attente).
        Aucun délai sur le premier chiffre : on attend indéfiniment tant que le
        combiné reste décroché — la tonalité, elle, tient pendant tout ce
        temps, et ne tombe qu'à la première impulsion (§1.2).

        Retourne le numéro composé, ou None si rien n'a été composé ou si le
        combiné a été raccroché.
        """
        self._set_state(STATE_RESTITUTION_NUMEROTATION)
        logger.info("Numérotation en cours (mode restitution)")
        digits: List[int] = []
        last_digit_ts: Optional[float] = None

        while self.inputs.is_hook_up():
            tonalite.tenir()
            digit = self.inputs.pop_digit()
            if digit is not None:
                digits.append(digit)
                last_digit_ts = time.monotonic()
                numero_partiel = "".join(map(str, digits))
                logger.info("Mode restitution, chiffre %d/%d composé : numéro %s",
                            len(digits), config.RESTITUTION_DIGITS_MAX, numero_partiel)
                self._set_state(STATE_RESTITUTION_NUMEROTATION, detail=numero_partiel)
                if len(digits) >= config.RESTITUTION_DIGITS_MAX:
                    logger.info("Numéro de %d chiffres atteint : saisie close.",
                                config.RESTITUTION_DIGITS_MAX)
                    break
            elif (last_digit_ts is not None
                    # is_dial_active() suspend l'inter-chiffre pendant tout le
                    # mouvement du cadran : sans ce garde, un numéro serait
                    # validé alors que le chiffre suivant est en cours de
                    # composition (le cadran met ~1 s à revenir au repos).
                    and not self.inputs.is_dial_active()
                    and (time.monotonic() - last_digit_ts) >= config.RESTITUTION_INTERDIGIT_SEC):
                logger.info("Cadran silencieux depuis %.1fs : numéro validé.",
                            config.RESTITUTION_INTERDIGIT_SEC)
                break
            time.sleep(MAIN_LOOP_POLL_SEC)

        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la numérotation (mode restitution), retour en attente.")
            return None
        if not digits:
            logger.info("Aucun chiffre composé (mode restitution), retour en attente.")
            return None

        # int() absorbe naturellement les zéros de tête (0 puis 1 -> 1).
        return int("".join(map(str, digits)))

    def _run_restitution_lecture(self, numero: int, messages: List[Path]) -> None:
        """Lit le message n° numero, borné à [1, N] (§5.7).

        Au-delà du nombre total de messages, le dernier est lu. Un numéro nul —
        le cadran rend 0 pour dix impulsions — est ramené au premier : un
        bornage symétrique est plus prévisible qu'un parcours d'erreur, et la
        règle demandée ne porte que sur la borne haute.
        """
        total = len(messages)
        index = min(max(numero, 1), total)
        if index != numero:
            logger.info("Numéro %d hors bornes (1-%d) : lecture du message n°%d.",
                        numero, total, index)
        message_path = messages[index - 1]
        self._set_state(STATE_RESTITUTION_LECTURE,
                        detail=f"{index}/{total} — {message_path.name}")
        logger.info("Mode restitution, lecture du message n°%d/%d : %s",
                    index, total, message_path.name)
        jouer_combine(message_path, self._hook_up_ignoring_dial,
                      device=config.RESTITUTION_SOUND_CARD)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la lecture (mode restitution), retour en attente.")
            return
        self._wait_for_hangup()

    def _wait_for_hangup(self) -> None:
        """Silence jusqu'au raccroché : cadran ignoré, aucun enregistrement (§5.7).

        Après un message, il faut raccrocher puis redécrocher pour en écouter un
        autre. Les chiffres composés entre-temps sont purgés par
        _hook_up_ignoring_dial() et restent sans effet.

        Cette attente est sans limite de durée — un combiné simplement posé à
        côté du téléphone y reste indéfiniment : c'est le seul état du parcours
        dont la durée n'est pas bornée par ailleurs, d'où le battement de
        status.json.
        """
        detail = "attente du raccroché"
        self._set_state(STATE_RESTITUTION_LECTURE, detail=detail)
        last_heartbeat = time.monotonic()
        while self._hook_up_ignoring_dial():
            last_heartbeat = self._battement(last_heartbeat, STATE_RESTITUTION_LECTURE,
                                             lambda: detail)
            time.sleep(MAIN_LOOP_POLL_SEC)

    def _run_enregistrement(self, should_continue: Callable[[], bool] = None) -> None:
        # Garde-fou du mode restitution (§5.7) : aucun des parcours de ce mode
        # n'arrive ici, mais une bascule pendant une communication déjà engagée
        # en mode mariage le pourrait. « Aucun enregistrement possible » est un
        # invariant du mode, il est donc vérifié ici aussi.
        # force=True : cet invariant doit être évalué sur l'état réel du
        # fichier, pas sur une valeur vieille d'au plus une seconde. L'appel est
        # unique par enregistrement, son coût est donc sans importance.
        if mode_io.is_restitution(force=True):
            logger.error("Enregistrement demandé en mode restitution : refusé (§5.7).")
            self._set_state(STATE_ENREGISTREMENT,
                            detail="enregistrement refusé (mode restitution)")
            return

        disk_state = disk_space_state(config.MESSAGES_DIR)
        if disk_state != "ok":
            logger.warning("Espace disque %s (seuils : alerte < %d Mo, critique < %d Mo).",
                            disk_state, config.DISK_WARNING_MB, config.DISK_CRITICAL_MB)
            status_io.write_status(STATE_ERREUR, detail=f"espace disque {disk_state}")

        if disk_state == "critique":
            logger.error("Enregistrement refusé : espace disque critique.")
            self._set_state(STATE_ENREGISTREMENT, detail="enregistrement refusé (espace disque critique)")
            return

        self._set_state(STATE_ENREGISTREMENT,
                         detail="espace disque faible" if disk_state == "alerte" else None)

        path = timestamped_recording_path()
        logger.info("Début de l'enregistrement : %s", path.name)

        # Gain de prise réglable à chaud depuis le dashboard : réappliqué
        # ici s'il a changé depuis le dernier enregistrement.
        alsa_io.set_mic_gain()
        confirmer = HangupConfirmer(self.inputs)

        def combined_should_continue() -> bool:
            if should_continue is not None and not should_continue():
                return False
            return confirmer.should_continue()

        result = audio_io.record(
            path, max_duration_sec=config.MAX_RECORD_SEC,
            should_continue=combined_should_continue,
            poll_interval=RECORD_POLL_SEC,
        )
        logger.info("Fin de l'enregistrement (%s) : %s", result, path.name)

        if path.exists():
            duration = wav_duration_sec(path)
            if duration < config.SHORT_RECORDING_THRESHOLD_SEC:
                logger.warning("Enregistrement très court conservé (%.1fs < %.0fs), non supprimé : %s",
                                duration, config.SHORT_RECORDING_THRESHOLD_SEC, path.name)
            # Traitement (ronflement 50 Hz, souffle, niveau) dans un processus
            # séparé, brut conservé dans messages/brut/ : la machine retourne
            # aussitôt en attente, un invité suivant peut décrocher.
            if config.TRAITEMENT_ACTIF and duration > 0:
                traitement_audio.lancer_en_arriere_plan(path)
        # Retour à l'attente : la boucle run_forever() relance _run_attente().


def run_test_mode() -> None:
    """Affiche en direct l'état des 3 GPIO, pour valider câblage et sens logiques (§7.6, point 3)."""
    try:
        gpio_io.configurer_broches()
    except RuntimeError:
        raise SystemExit("RPi.GPIO indisponible : le mode --test doit être exécuté sur le Raspberry Pi.")

    print("Mode test GPIO — décrochez / tournez le cadran pour valider le câblage. Ctrl+C pour quitter.\n")
    print(f"HOOK_PIN={config.HOOK_PIN} (actif={config.HOOK_ACTIVE_STATE}) | "
          f"DIAL_OFFNORMAL_PIN={config.DIAL_OFFNORMAL_PIN} | "
          f"DIAL_PULSE_PIN={config.DIAL_PULSE_PIN} (actif niveau {config.PULSE_ACTIF_LEVEL})\n")
    try:
        while True:
            etats = gpio_io.lire_contacts()
            hook_state = "DECROCHE" if etats["crochet"] else "raccroché"
            offnormal_state = "actif (cadran en mouvement)" if etats["off-normal"] else "repos"
            pulse_state = "actif" if etats["impulsions"] else "repos"
            print(f"\rcrochet={hook_state:<10} | off-normal={offnormal_state:<25} | impulsion={pulse_state:<8}",
                  end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nArrêt du mode test.")
    finally:
        gpio_io.GPIO.cleanup()


def _check_required_audio_files() -> None:
    """Refuse de démarrer sans le message générique et le bip, au minimum (§7.2)."""
    required = [config.BIP_WAV, config.MESSAGE_GENERIQUE_WAV]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        detail = "Fichiers audio requis manquants : " + ", ".join(missing)
        status_io.write_status(STATE_ERREUR, detail=detail)
        raise SystemExit(detail + " — lancez d'abord `python3 prepare_audio.py`.")


def _check_audio_format() -> None:
    """Signale les fichiers restés à l'ancien format (§4.2), sans bloquer le démarrage.

    Détecteur de migration : avant le nouveau câblage, audio/ contenait des
    WAV 44,1 kHz panés à 100 % sur un canal. Joués sur le montage actuel, ils
    seraient muets d'un côté — la sonnerie ne sortirait pas du haut-parleur,
    ou un seul écouteur fonctionnerait. Cinq lectures d'en-tête au démarrage
    coûtent moins qu'un mariage silencieux.
    """
    perimes = []
    for path in sorted(config.AUDIO_DIR.glob("*.wav")):
        try:
            with wave.open(str(path), "rb") as wav:
                conforme = (wav.getnchannels() == config.AUDIO_CHANNELS
                            and wav.getframerate() == config.AUDIO_RATE_HZ)
        except (wave.Error, OSError):
            perimes.append(f"{path.name} (illisible)")
            continue
        if not conforme:
            perimes.append(path.name)

    if perimes:
        detail = ("Fichiers audio à reconvertir (format attendu : "
                  f"{config.AUDIO_RATE_HZ} Hz, {config.AUDIO_CHANNELS} canaux) : "
                  + ", ".join(perimes))
        logger.warning("%s — lancez `python3 src/prepare_audio.py` ou « Tout "
                       "reconvertir » depuis le dashboard.", detail)


def _wait_for_sound_card(poll_sec: float = 2.0) -> None:
    """Attend que la carte son configurée soit énumérée (§7.2) : ne renonce jamais, retente périodiquement."""
    if audio_io.sound_card_available():
        return
    detail = f"carte son {config.SOUND_CARD} introuvable"
    logger.error("%s, nouvelle tentative toutes les %.0fs...", detail, poll_sec)
    status_io.write_status(STATE_ERREUR, detail=detail)
    while not audio_io.sound_card_available():
        time.sleep(poll_sec)
    logger.info("Carte son %s détectée.", config.SOUND_CARD)


def niveau_log(nom: Optional[str] = None) -> int:
    """Niveau logging à partir de son nom ; INFO si la valeur est incomprise.

    Tolérant volontairement (§7.4) : ce réglage vient de custom_config.json,
    que le dashboard écrit mais qu'une main humaine peut aussi avoir édité. Un
    « Debug » ou un « debug » doit marcher, et une faute de frappe ne doit pas
    empêcher le service de démarrer — elle le laisse au niveau d'exploitation.
    """
    nom = config.LOG_LEVEL if nom is None else nom
    niveau = logging.getLevelName(str(nom).strip().upper())
    if not isinstance(niveau, int):
        logger.warning("Niveau de journalisation inconnu (%r) : INFO retenu.", nom)
        return logging.INFO
    return niveau


def setup_logging(niveau: Optional[int] = None) -> None:
    """Console + fichier avec rotation (5 x 1 Mo) sur logs/livre_dor.log (§7.3)."""
    config.ensure_directories()
    fichiers.configurer_journal(config.LIVRE_DOR_LOG,
                                niveau_log() if niveau is None else niveau)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true",
                         help="Mode test GPIO (affichage temps réel des 3 broches, §7.6).")
    parser.add_argument("--verbeux", "-v", action="store_true",
                         help="Journalise le détail du cadran et des sorties audio "
                              "(équivaut à LOG_LEVEL=DEBUG, le temps d'un lancement à la main).")
    args = parser.parse_args()

    config.ensure_directories()

    if args.test:
        # Mode diagnostic hors ligne : pas de logs fichier ni de status.json.
        logging.basicConfig(level=logging.DEBUG if args.verbeux else logging.INFO,
                            format=fichiers.FORMAT_JOURNAL)
        run_test_mode()
        return

    # --verbeux l'emporte sur la config : il sert à un lancement à la main,
    # sans toucher au réglage que systemd relira au prochain démarrage.
    setup_logging(logging.DEBUG if args.verbeux else None)
    logger.info("Journalisation au niveau %s.",
                logging.getLevelName(logging.getLogger().level))
    mode_io.ensure_config_exists()
    logger.info("Mode de fonctionnement : %s", mode_io.mode_label())
    _wait_for_sound_card()
    # Configuration complète du codec (entrée micro, routage, ALC off) : une
    # seule fois ici, après l'énumération de la carte. Un avertissement et non
    # un arrêt — sur une carte déjà figée par `alsactl restore` au démarrage,
    # le téléphone fonctionne parfaitement.
    if not alsa_io.setup_card(config.AUDIO_OUTPUT_COMBINE):
        logger.warning("Configuration ALSA (%s) non appliquée : vérifiez les "
                       "sorties avec `%s status`.",
                       config.AUDIO_SETUP_SCRIPT, config.AUDIO_SETUP_SCRIPT)
    _check_required_audio_files()
    _check_audio_format()
    # Récapitulatif de ce que le service va réellement utiliser. Un « aucun
    # son » se diagnostique d'abord ici : carte, sorties et format effectifs,
    # qui viennent de custom_config.json autant que des valeurs par défaut.
    logger.info("Audio : carte %s, sonnerie sur %s (volume %d %%), combiné sur %s "
                "(volume %d %%), %d Hz %d canaux ; tonalité bornée à %d s.",
                config.SOUND_CARD, config.AUDIO_OUTPUT_SONNERIE, config.VOLUME_SONNERIE,
                config.AUDIO_OUTPUT_COMBINE, config.VOLUME_COMBINE, config.AUDIO_RATE_HZ,
                config.AUDIO_CHANNELS, config.TONALITE_MAX_SEC)

    inputs = gpio_io.PhoneInputs()
    gpio_io.setup(inputs)
    try:
        GuestBookStateMachine(inputs).run_forever()
    except Exception:
        logger.exception("Exception non gérée dans la machine à états, arrêt du service.")
        status_io.write_status(STATE_ERREUR, detail="exception non gérée, voir logs/livre_dor.log")
        raise
    finally:
        gpio_io.cleanup()


if __name__ == "__main__":
    main()
