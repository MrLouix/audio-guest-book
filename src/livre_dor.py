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
"""

import argparse
import datetime
import logging
import logging.handlers
import random
import shutil
import time
import wave
from pathlib import Path
from typing import Callable, List, Optional

import audio_io
import config
import gpio_io
import status_io

logger = logging.getLogger(__name__)

STATE_ATTENTE = "attente"
STATE_SONNERIE = "sonnerie"
STATE_DECROCHE = "decroche"
STATE_NUMEROTATION = "numerotation"
STATE_LECTURE_MESSAGE = "lecture_message"
STATE_APPEL_REPONDU = "appel_repondu"
STATE_ENREGISTREMENT = "enregistrement"
STATE_ERREUR = "erreur"

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


def available_random_messages() -> List[Path]:
    """Tous les messages des mariés pouvant être tirés au hasard (§1.2) : message_N.wav + message_generique.wav."""
    candidates = [config.message_wav(d) for d in range(10) if config.message_wav(d).exists()]
    if config.MESSAGE_GENERIQUE_WAV.exists():
        candidates.append(config.MESSAGE_GENERIQUE_WAV)
    return candidates


def consume_ring_trigger() -> bool:
    """Consomme le fichier drapeau ring_trigger s'il existe (déclenchement à distance, §5.1, §8).

    Traitement atomique : tester l'existence puis supprimer, en tolérant une
    suppression concurrente (ex. par un autre processus) sans lever d'erreur.
    """
    if not config.RING_TRIGGER_FILE.exists():
        return False
    try:
        config.RING_TRIGGER_FILE.unlink()
    except FileNotFoundError:
        return False
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


class GuestBookStateMachine:
    """Boucle des états du parcours invité, nominal et « appel entrant » (§1.2, §5.1)."""

    def __init__(self, inputs: gpio_io.PhoneInputs) -> None:
        self.inputs = inputs
        self.state = STATE_ATTENTE
        self._ring_active = False
        self._last_ring_end_ts: Optional[float] = None
        self._last_ring_start_ts = time.monotonic()
        self._last_random_message: Optional[Path] = None

    def _set_state(self, state: str, detail: Optional[str] = None) -> None:
        self.state = state
        status_io.write_status(state, detail)

    def run_forever(self) -> None:
        logger.info("Machine à états démarrée, état initial : %s", self.state)
        while True:
            self._run_attente()

    def _is_recent_ring(self) -> bool:
        """Drapeau « sonnerie récente » (§5.1) : actif pendant la sonnerie et RING_ANSWER_GRACE_SEC après."""
        if self._ring_active:
            return True
        if self._last_ring_end_ts is None:
            return False
        return (time.monotonic() - self._last_ring_end_ts) <= config.RING_ANSWER_GRACE_SEC

    def _run_attente(self) -> None:
        self._set_state(STATE_ATTENTE)
        self._last_ring_start_ts = time.monotonic()
        last_heartbeat = time.monotonic()
        while True:
            if self.inputs.is_hook_up():
                if self._is_recent_ring():
                    self._run_appel_repondu()
                else:
                    self._run_decroche()
                return
            if consume_ring_trigger():
                logger.info("Sonnerie déclenchée à distance (ring_trigger)")
                self._run_sonnerie()
                continue
            if (time.monotonic() - self._last_ring_start_ts) >= config.RING_INTERVAL_SEC:
                self._run_sonnerie()
                continue
            if (time.monotonic() - last_heartbeat) >= config.STATUS_HEARTBEAT_SEC:
                status_io.write_status(STATE_ATTENTE)
                last_heartbeat = time.monotonic()
            time.sleep(MAIN_LOOP_POLL_SEC)

    def _run_sonnerie(self) -> None:
        self._set_state(STATE_SONNERIE)
        logger.info("Sonnerie")
        self._ring_active = True
        try:
            audio_io.play(
                config.RING_OUT_WAV,
                should_continue=lambda: not self.inputs.is_hook_up(),
                timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
            )
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
        audio_io.play(message_path, should_continue=self._hook_up_ignoring_dial,
                      timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le message (appel répondu), retour en attente.")
            return

        audio_io.play(config.BIP_WAV, should_continue=self._hook_up_ignoring_dial,
                      timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le bip (appel répondu), retour en attente.")
            return

        self._run_enregistrement(should_continue=self._hook_up_ignoring_dial)

    def _run_decroche(self) -> None:
        self._set_state(STATE_DECROCHE)
        logger.info("Décroché : tonalité")
        self.inputs.reset_dial()
        audio_io.play(
            config.TONALITE_WAV,
            should_continue=lambda: self.inputs.is_hook_up() and not self.inputs.has_pulses(),
            timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
        )
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant la tonalité, retour en attente.")
            return
        # La tonalité s'est arrêtée dès la première impulsion détectée (§1.2).
        self._run_numerotation()

    def _run_numerotation(self) -> None:
        self._set_state(STATE_NUMEROTATION)
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
        message_path = message_path_for_digit(digit)
        self._set_state(STATE_LECTURE_MESSAGE, detail=message_path.name)
        logger.info("Lecture du message : %s", message_path.name)
        audio_io.play(message_path, should_continue=self.inputs.is_hook_up,
                      timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le message, retour en attente.")
            return

        audio_io.play(config.BIP_WAV, should_continue=self.inputs.is_hook_up,
                      timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
        if not self.inputs.is_hook_up():
            logger.info("Raccroché pendant le bip, retour en attente.")
            return

        self._run_enregistrement()

    def _run_enregistrement(self, should_continue: Callable[[], bool] = None) -> None:
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

        confirmer = HangupConfirmer(self.inputs)

        def combined_should_continue() -> bool:
            if should_continue is not None and not should_continue():
                return False
            return confirmer.should_continue()

        result = audio_io.record(
            path, max_duration_sec=config.MAX_RECORD_SEC,
            should_continue=combined_should_continue,
            poll_interval=0.02,
        )
        logger.info("Fin de l'enregistrement (%s) : %s", result, path.name)

        if path.exists():
            duration = wav_duration_sec(path)
            if duration < config.SHORT_RECORDING_THRESHOLD_SEC:
                logger.warning("Enregistrement très court conservé (%.1fs < %.0fs), non supprimé : %s",
                                duration, config.SHORT_RECORDING_THRESHOLD_SEC, path.name)
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
    """Refuse de démarrer sans le message générique et le bip, au minimum (§7.2)."""
    required = [config.BIP_WAV, config.MESSAGE_GENERIQUE_WAV]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        detail = "Fichiers audio requis manquants : " + ", ".join(missing)
        status_io.write_status(STATE_ERREUR, detail=detail)
        raise SystemExit(detail + " — lancez d'abord `python3 prepare_audio.py`.")


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


def setup_logging() -> None:
    """Console + fichier avec rotation (5 x 1 Mo) sur logs/livre_dor.log (§7.3)."""
    config.ensure_directories()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        config.LIVRE_DOR_LOG, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true",
                         help="Mode test GPIO (affichage temps réel des 3 broches, §7.6).")
    args = parser.parse_args()

    config.ensure_directories()

    if args.test:
        # Mode diagnostic hors ligne : pas de logs fichier ni de status.json.
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        run_test_mode()
        return

    setup_logging()
    _wait_for_sound_card()
    _check_required_audio_files()

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
