"""Test logique du mode restitution (§5.7), sans Raspberry Pi ni carte son.

Complète src/load_test.py, qui valide le parcours mariage sur le matériel
final : ici on valide les *règles* du mode restitution — numérotation
multi-chiffres, bornage du numéro, ordre chronologique, et surtout
l'invariant « aucun enregistrement possible ». Le test tourne sur n'importe
quelle machine (poste de développement inclus) :

- gpio_io.PhoneInputs est piloté à la main, comme dans load_test.py, en
  reproduisant la séquence réelle du cadran (off-normal, impulsions, retour
  au repos) ;
- audio_io.play et audio_io.record sont remplacés par des doublures qui
  enregistrent leurs appels au lieu de lancer aplay/arecord. C'est ce qui
  rend les vérifications déterministes : sans doublure, aplay étant absent,
  play() renverrait « error » immédiatement et tous les délais seraient faux.

messages/ n'est jamais touché : chaque scénario travaille dans un dossier
temporaire, supprimé à la fin.

Usage :
    python3 src/restitution_test.py
"""

import datetime
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

import audio_io
import config
import gpio_io
import livre_dor
import mode_io
import status_io

logger = logging.getLogger(__name__)

# Inter-chiffre raccourci : le test reste rapide tout en laissant au cadran
# simulé le temps de composer un chiffre complet.
TEST_INTERDIGIT_SEC = 0.4
PULSE_GAP_SEC = 0.01
SETTLE_SEC = 0.25
PLAY_DURATION_SEC = 0.3


class FakeAudio:
    """Doublure de audio_io.play/record : journalise les appels, ne lance aucun processus."""

    def __init__(self) -> None:
        self.played: List[Path] = []
        self.recorded: List[Path] = []
        self.interrupted: List[Path] = []
        self._lock = threading.Lock()

    def play(self, path, should_continue, poll_interval=0.1, timeout_sec=None, device=None) -> str:
        with self._lock:
            self.played.append(Path(path))
        deadline = time.monotonic() + PLAY_DURATION_SEC
        while time.monotonic() < deadline:
            if not should_continue():
                with self._lock:
                    self.interrupted.append(Path(path))
                return "interrupted"
            time.sleep(0.02)
        return "completed"

    def record(self, path, max_duration_sec, should_continue, poll_interval=0.1) -> str:
        with self._lock:
            self.recorded.append(Path(path))
        return "completed"

    def names_played(self) -> List[str]:
        with self._lock:
            return [p.name for p in self.played]


class Scenario:
    """Contexte d'un scénario : dossier de messages temporaire + machine à états sur un thread."""

    def __init__(self, message_count: int, restitution: bool = True,
                 ring_interval: int = 10 ** 9, heartbeat_sec: float = None) -> None:
        self.message_count = message_count
        self.restitution = restitution
        # Sonnerie neutralisée par défaut : un scénario qui n'étudie pas la
        # sonnerie n'a pas à en déclencher une au bout de RING_INTERVAL_SEC.
        self.ring_interval = ring_interval
        self.heartbeat_sec = heartbeat_sec
        self.audio = FakeAudio()
        self.inputs = gpio_io.PhoneInputs()
        self.expected_names: List[str] = []

    def __enter__(self) -> "Scenario":
        self._tmpdir = tempfile.TemporaryDirectory(prefix="restitution_test_")
        tmp = Path(self._tmpdir.name)
        self._saved = {
            "MESSAGES_DIR": config.MESSAGES_DIR,
            "MODE_CONFIG_FILE": config.MODE_CONFIG_FILE,
            "STATUS_FILE": config.STATUS_FILE,
            "RESTITUTION_INTERDIGIT_SEC": config.RESTITUTION_INTERDIGIT_SEC,
            "RING_INTERVAL_SEC": config.RING_INTERVAL_SEC,
            "STATUS_HEARTBEAT_SEC": config.STATUS_HEARTBEAT_SEC,
            "play": audio_io.play,
            "record": audio_io.record,
        }
        config.MESSAGES_DIR = tmp / "messages"
        config.MESSAGES_DIR.mkdir()
        config.MODE_CONFIG_FILE = tmp / "mode_config.json"
        # status.json est écrit en continu par la machine à états : le rediriger
        # évite de polluer celui du dépôt pendant les tests.
        config.STATUS_FILE = tmp / "status.json"
        config.RESTITUTION_INTERDIGIT_SEC = TEST_INTERDIGIT_SEC
        config.RING_INTERVAL_SEC = self.ring_interval
        if self.heartbeat_sec is not None:
            config.STATUS_HEARTBEAT_SEC = self.heartbeat_sec
        audio_io.play = self.audio.play
        audio_io.record = self.audio.record
        mode_io.write_mode(self.restitution)

        # Enregistrements factices, créés dans le désordre pour que le test
        # dépende du tri de recorded_messages() et non de l'ordre de création.
        base = datetime.datetime(2026, 6, 20, 14, 0, 0)
        names = [(base + datetime.timedelta(minutes=i)).strftime("message_%Y-%m-%d_%H-%M-%S.wav")
                 for i in range(self.message_count)]
        self.expected_names = list(names)
        for name in reversed(names):
            (config.MESSAGES_DIR / name).write_bytes(b"RIFF")

        self.machine = livre_dor.GuestBookStateMachine(self.inputs)
        self._thread = threading.Thread(target=self.machine.run_forever, daemon=True)
        self._thread.start()
        time.sleep(SETTLE_SEC)
        return self

    def __exit__(self, *exc_info) -> None:
        self.inputs.set_hook(False)
        time.sleep(SETTLE_SEC)
        # Arrêter la machine avant de rendre le dossier temporaire, sinon elle
        # continuerait d'écrire status.json dans un chemin supprimé.
        self.machine.request_stop()
        self._thread.join(timeout=5.0)
        config.MESSAGES_DIR = self._saved["MESSAGES_DIR"]
        config.MODE_CONFIG_FILE = self._saved["MODE_CONFIG_FILE"]
        config.STATUS_FILE = self._saved["STATUS_FILE"]
        config.RESTITUTION_INTERDIGIT_SEC = self._saved["RESTITUTION_INTERDIGIT_SEC"]
        config.RING_INTERVAL_SEC = self._saved["RING_INTERVAL_SEC"]
        config.STATUS_HEARTBEAT_SEC = self._saved["STATUS_HEARTBEAT_SEC"]
        audio_io.play = self._saved["play"]
        audio_io.record = self._saved["record"]
        mode_io.invalidate_cache()
        self._tmpdir.cleanup()

    # --- Pilotage du téléphone simulé ---------------------------------

    def lift(self) -> None:
        self.inputs.set_hook(True)
        time.sleep(SETTLE_SEC)

    def hangup(self) -> None:
        self.inputs.set_hook(False)
        time.sleep(SETTLE_SEC)

    def dial(self, *digits: int) -> None:
        """Compose des chiffres au cadran, séquence identique à load_test._dial_digit."""
        for digit in digits:
            n_pulses = 10 if digit == 0 else digit
            self.inputs.set_dial_active(True)
            for _ in range(n_pulses):
                time.sleep(PULSE_GAP_SEC)
                self.inputs.register_pulse()
            time.sleep(PULSE_GAP_SEC)
            self.inputs.set_dial_active(False)
            time.sleep(0.05)

    def wait_interdigit(self) -> None:
        time.sleep(TEST_INTERDIGIT_SEC + 3 * SETTLE_SEC)

    def guest_messages_played(self) -> List[str]:
        """Noms des seuls enregistrements d'invités joués (hors tonalité, bip, annonces)."""
        expected = set(self.expected_names)
        return [name for name in self.audio.names_played() if name in expected]


# --- Assertions ---------------------------------------------------------

FAILURES: List[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ÉCHEC {label}" + (f" — {detail}" if detail else ""))
        FAILURES.append(label)


# --- Scénarios ----------------------------------------------------------

def scenario_un_chiffre_puis_attente() -> None:
    print("1. « 1 » puis attente -> premier message chronologique")
    with Scenario(message_count=20) as sc:
        sc.lift()
        sc.dial(1)
        sc.wait_interdigit()
        played = sc.guest_messages_played()
        check("le 1er message est lu", played == [sc.expected_names[0]], f"joué : {played}")
        check("aucun enregistrement", sc.audio.recorded == [])


def scenario_quatre_chiffres() -> None:
    print("2. « 3695 » -> saisie close au 4e chiffre, 5e ignoré, dernier message lu")
    with Scenario(message_count=20) as sc:
        sc.lift()
        sc.dial(3, 6, 9, 5)
        time.sleep(3 * SETTLE_SEC)          # sans attendre l'inter-chiffre
        played_apres_quatre = sc.guest_messages_played()
        check("la lecture démarre sans attendre l'inter-chiffre",
              played_apres_quatre == [sc.expected_names[-1]], f"joué : {played_apres_quatre}")
        sc.dial(7)                           # 5e chiffre : doit rester sans effet
        time.sleep(3 * SETTLE_SEC)
        check("le 5e chiffre est ignoré",
              sc.guest_messages_played() == [sc.expected_names[-1]],
              f"joué : {sc.guest_messages_played()}")
        check("aucun enregistrement", sc.audio.recorded == [])


def scenario_numero_dans_les_bornes() -> None:
    print("3. « 12 » avec 20 messages -> 12e message")
    with Scenario(message_count=20) as sc:
        sc.lift()
        sc.dial(1, 2)
        sc.wait_interdigit()
        played = sc.guest_messages_played()
        check("le 12e message est lu", played == [sc.expected_names[11]], f"joué : {played}")


def scenario_zero() -> None:
    print("4. « 0 » seul (10 impulsions) -> premier message (bornage bas)")
    with Scenario(message_count=5) as sc:
        sc.lift()
        sc.dial(0)
        sc.wait_interdigit()
        played = sc.guest_messages_played()
        check("le 1er message est lu", played == [sc.expected_names[0]], f"joué : {played}")


def scenario_aucun_message() -> None:
    print("5. messages/ vide -> annonce d'absence, aucune exception")
    with Scenario(message_count=0) as sc:
        sc.lift()
        time.sleep(3 * SETTLE_SEC)
        played = sc.audio.names_played()
        check("une annonce est jouée", len(played) >= 1, f"joué : {played}")
        check("la tonalité n'est pas jouée", "tonalite.wav" not in played, f"joué : {played}")
        check("aucun enregistrement", sc.audio.recorded == [])
        sc.hangup()


def scenario_raccroche_pendant_saisie() -> None:
    print("6. raccroché pendant la saisie -> rien n'est lu")
    with Scenario(message_count=5) as sc:
        sc.lift()
        sc.dial(2)
        sc.hangup()
        sc.wait_interdigit()
        check("aucun message d'invité lu", sc.guest_messages_played() == [],
              f"joué : {sc.guest_messages_played()}")
        check("aucun enregistrement", sc.audio.recorded == [])


def scenario_raccroche_pendant_lecture() -> None:
    print("7. raccroché pendant la lecture -> lecture interrompue")
    with Scenario(message_count=5) as sc:
        sc.lift()
        sc.dial(2)
        # Raccroché pendant que la doublure joue encore le message.
        time.sleep(TEST_INTERDIGIT_SEC + SETTLE_SEC)
        sc.inputs.set_hook(False)
        time.sleep(3 * SETTLE_SEC)
        interrupted = [p.name for p in sc.audio.interrupted]
        check("la lecture du message est interrompue",
              sc.expected_names[1] in interrupted, f"interrompu : {interrupted}")


def scenario_chiffre_pendant_lecture() -> None:
    print("8. chiffre composé pendant la lecture -> ignoré (silence jusqu'au raccroché)")
    with Scenario(message_count=5) as sc:
        sc.lift()
        sc.dial(2)
        sc.wait_interdigit()
        sc.dial(4)
        time.sleep(4 * SETTLE_SEC)
        played = sc.guest_messages_played()
        check("un seul message est lu", played == [sc.expected_names[1]], f"joué : {played}")


def scenario_jamais_de_sonnerie_ni_enregistrement() -> None:
    print("9. mode restitution prolongé -> ni sonnerie ni enregistrement")
    # ring_interval=1 : en mode mariage la sonnerie partirait immédiatement.
    with Scenario(message_count=3, ring_interval=1) as sc:
        time.sleep(1.5)                   # attente prolongée, combiné raccroché
        played = sc.audio.names_played()
        check("la sonnerie n'est jamais jouée", "ring_out.wav" not in played,
              f"joué : {played}")
        check("aucun enregistrement", sc.audio.recorded == [])


def scenario_garde_fou_enregistrement() -> None:
    print("10. bascule du mode pendant un appel mariage -> enregistrement refusé")
    with Scenario(message_count=3, restitution=False) as sc:
        mode_io.write_mode(True)          # bascule pendant la communication
        sc.machine._run_enregistrement()
        check("aucun enregistrement créé", sc.audio.recorded == [],
              f"enregistré : {sc.audio.recorded}")
        check("aucun fichier ajouté dans messages/",
              len(list(config.MESSAGES_DIR.glob("*.wav"))) == sc.message_count)


def scenario_ordre_chronologique() -> None:
    print("11. recorded_messages() : ordre chronologique, collisions, noms non conformes")
    saved = config.MESSAGES_DIR
    with tempfile.TemporaryDirectory(prefix="restitution_tri_") as tmp:
        config.MESSAGES_DIR = Path(tmp)
        try:
            noms = [
                "message_2026-06-20_14-05-00.wav",
                "message_2026-06-20_14-00-00.wav",
                "message_2026-06-20_14-00-00_1.wav",
                "message_2026-06-20_14-00-00_2.wav",
                "message_2026-06-20_14-00-00_10.wav",
                "message_2026-06-20_13-00-00.wav",
            ]
            for nom in noms:
                (config.MESSAGES_DIR / nom).write_bytes(b"RIFF")

            # Nom non conforme : trié par mtime, ici forcée entre 13h et 14h.
            hors_norme = config.MESSAGES_DIR / "souvenir_tante_jeanne.wav"
            hors_norme.write_bytes(b"RIFF")
            cible = datetime.datetime(2026, 6, 20, 13, 30, 0).timestamp()
            os.utime(hors_norme, (cible, cible))

            # Doivent être exclus : transcription et sous-dossier.
            (config.MESSAGES_DIR / "message_2026-06-20_14-00-00.txt").write_text("bonjour")
            (config.MESSAGES_DIR / "sous_dossier").mkdir()

            obtenu = [p.name for p in livre_dor.recorded_messages()]
            attendu = [
                "message_2026-06-20_13-00-00.wav",
                "souvenir_tante_jeanne.wav",
                "message_2026-06-20_14-00-00.wav",
                "message_2026-06-20_14-00-00_1.wav",
                "message_2026-06-20_14-00-00_2.wav",
                "message_2026-06-20_14-00-00_10.wav",
                "message_2026-06-20_14-05-00.wav",
            ]
            check("ordre chronologique complet", obtenu == attendu,
                  f"obtenu : {obtenu}")
            check("le .txt de transcription est exclu",
                  all(not n.endswith(".txt") for n in obtenu))
            check("le sous-dossier est ignoré", "sous_dossier" not in obtenu)
        finally:
            config.MESSAGES_DIR = saved


def scenario_cache_du_mode() -> None:
    print("12. relecture du mode : cache de MODE_RELOAD_SEC, invalidé à l'écriture")
    tmpdir = tempfile.TemporaryDirectory(prefix="restitution_cache_")
    saved_file, saved_ttl = config.MODE_CONFIG_FILE, config.MODE_RELOAD_SEC
    saved_read = mode_io._read_file
    lectures = []
    try:
        config.MODE_CONFIG_FILE = Path(tmpdir.name) / "mode_config.json"
        config.MODE_RELOAD_SEC = 0.5
        mode_io._read_file = lambda: (lectures.append(1), saved_read())[1]
        mode_io.write_mode(False)

        lectures.clear()
        for _ in range(40):                       # deux secondes de boucle à 20 Hz
            mode_io.is_restitution()
        check("40 appels rapprochés ne relisent le fichier qu'une fois",
              len(lectures) == 1, f"{len(lectures)} lecture(s)")

        lectures.clear()
        mode_io.is_restitution(force=True)
        check("force=True court-circuite le cache", len(lectures) == 1,
              f"{len(lectures)} lecture(s)")

        # Bascule par le dashboard : visible tout de suite, sans attendre le TTL.
        mode_io.write_mode(True)
        check("une bascule locale est vue immédiatement", mode_io.is_restitution() is True)

        # Bascule écrite par un autre processus : vue après expiration du TTL.
        config.MODE_CONFIG_FILE.write_text('{"restitution": false}', encoding="utf-8")
        check("valeur encore en cache juste après", mode_io.is_restitution() is True)
        time.sleep(config.MODE_RELOAD_SEC + 0.15)
        check("relue après expiration du cache", mode_io.is_restitution() is False)
    finally:
        mode_io._read_file = saved_read
        config.MODE_CONFIG_FILE, config.MODE_RELOAD_SEC = saved_file, saved_ttl
        mode_io.invalidate_cache()
        tmpdir.cleanup()


def scenario_heartbeat_combine_decroche() -> None:
    print("13. combiné laissé décroché -> status.json continue d'être rafraîchi")
    # Sans heartbeat dans _wait_for_hangup, le watchdog (§7.1) verrait
    # status.json périmé et redémarrerait le service en boucle.
    ecritures = []
    saved_write = status_io.write_status
    status_io.write_status = lambda etat, detail=None: (
        ecritures.append((etat, detail)), saved_write(etat, detail))[1]
    try:
        with Scenario(message_count=5, heartbeat_sec=0.3) as sc:
            sc.lift()
            sc.dial(2)
            sc.wait_interdigit()
            time.sleep(PLAY_DURATION_SEC + SETTLE_SEC)
            ecritures.clear()
            time.sleep(1.2)                       # combiné toujours décroché
            battements = [e for e in ecritures
                          if e == (livre_dor.STATE_RESTITUTION_LECTURE, "attente du raccroché")]
            check("status.json rafraîchi pendant l'attente du raccroché",
                  len(battements) >= 2, f"{len(battements)} battement(s) en 1,2s")
            check("aucun enregistrement", sc.audio.recorded == [])
    finally:
        status_io.write_status = saved_write


SCENARIOS = [
    scenario_un_chiffre_puis_attente,
    scenario_quatre_chiffres,
    scenario_numero_dans_les_bornes,
    scenario_zero,
    scenario_aucun_message,
    scenario_raccroche_pendant_saisie,
    scenario_raccroche_pendant_lecture,
    scenario_chiffre_pendant_lecture,
    scenario_jamais_de_sonnerie_ni_enregistrement,
    scenario_garde_fou_enregistrement,
    scenario_ordre_chronologique,
    scenario_cache_du_mode,
    scenario_heartbeat_combine_decroche,
]


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    print("Test logique du mode restitution (§5.7) — aucun matériel requis\n")
    for scenario in SCENARIOS:
        scenario()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} vérification(s) en échec : " + ", ".join(FAILURES))
        raise SystemExit(1)
    print("Toutes les vérifications passent.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
