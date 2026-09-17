"""Boîte à outils commune aux scripts de test unitaires de tests/ (§7.6).

Ce module ne contient **aucun test** : uniquement ce que les scripts de ce
dossier partagent — le rapport affiché dans le terminal, le téléphone simulé
(GPIO pilotés à la main), les doublures audio, et les quelques aides pour les
tests sur matériel réel (`--reel`).

Chaque script de tests/ reste autonome : il s'exécute seul, affiche son propre
rapport, et sort avec le code 0 (tout passe) ou 1 (au moins un échec).

Principe des tests simulés, repris de src/restitution_test.py :

- `gpio_io.PhoneInputs` est piloté à la main, en reproduisant la séquence
  réelle du cadran (off-normal, impulsions, retour au repos) ;
- `audio_io.play` / `audio_io.record` sont remplacés par des doublures qui
  journalisent leurs appels au lieu de lancer aplay/arecord — sans elles,
  aplay étant absent d'un poste de développement, play() renverrait « error »
  immédiatement et tous les délais seraient faux ;
- toute l'arborescence de données (audio/, messages/, status.json,
  mode_config.json, ring_trigger) est redirigée vers un dossier temporaire,
  supprimé à la fin : les vrais messages des invités ne sont jamais touchés.
"""

import contextlib
import datetime
import io
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

RACINE = Path(__file__).resolve().parent.parent
SRC = RACINE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import audio_io    # noqa: E402  (src/ ajouté au sys.path juste au-dessus)
import config      # noqa: E402
import gpio_io     # noqa: E402
import livre_dor   # noqa: E402
import mode_io     # noqa: E402
import status_io   # noqa: E402

# --- Réglages temporels des scénarios simulés ---------------------------

# Marge laissée à la machine à états (boucle à 20 Hz) pour réagir.
STABILISATION_SEC = 0.25
# Espacement des impulsions du cadran simulé.
PULSE_GAP_SEC = 0.01
# Durée d'une « lecture » de la doublure audio : assez longue pour qu'un
# raccroché puisse tomber au milieu, assez courte pour que les tests soient
# rapides.
DUREE_LECTURE_SEC = 0.3


# --- Rapport terminal ---------------------------------------------------

def _couleurs_actives() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _unicode_supporte() -> bool:
    try:
        "✔✘─▶".encode(getattr(sys.stdout, "encoding", None) or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


_COULEUR = _couleurs_actives()
_UNICODE = _unicode_supporte()

VERT = "\033[32m" if _COULEUR else ""
ROUGE = "\033[31m" if _COULEUR else ""
JAUNE = "\033[33m" if _COULEUR else ""
GRIS = "\033[90m" if _COULEUR else ""
GRAS = "\033[1m" if _COULEUR else ""
RAZ = "\033[0m" if _COULEUR else ""

_OK = "✔" if _UNICODE else "OK  "
_KO = "✘" if _UNICODE else "ECHEC"
_SAUT = "~" if _UNICODE else "-"
_FLECHE = "▶" if _UNICODE else ">"
_TRAIT = "─" if _UNICODE else "-"
_DOUBLE = "═" if _UNICODE else "="


class Rapport:
    """Compte les vérifications d'un script et les affiche lisiblement.

    Usage :

        rapport = Rapport("DÉCROCHÉ", "passage au décroché et tonalité")
        rapport.section("1. Contact du crochet")
        rapport.verifie("le décroché est vu", inputs.is_hook_up() is True)
        rapport.conclure()     # affiche le bilan et sort avec 0 ou 1
    """

    def __init__(self, titre: str, sous_titre: str = "") -> None:
        self.titre = titre
        self.sous_titre = sous_titre
        self.reussites = 0
        self.echecs: List[str] = []
        self.ignores: List[str] = []
        self._debut = time.monotonic()
        largeur = 70
        print()
        print(GRAS + _DOUBLE * largeur + RAZ)
        entete = f" {titre}"
        if sous_titre:
            entete += f" — {sous_titre}"
        print(GRAS + entete + RAZ)
        print(GRAS + _DOUBLE * largeur + RAZ)

    def section(self, titre: str) -> None:
        print(f"\n{GRAS}{_FLECHE} {titre}{RAZ}")

    def verifie(self, libelle: str, condition: bool, detail: str = "") -> bool:
        """Enregistre une vérification ; retourne la condition, pour chaîner."""
        if condition:
            self.reussites += 1
            print(f"  {VERT}{_OK}{RAZ} {libelle}")
        else:
            self.echecs.append(libelle)
            print(f"  {ROUGE}{_KO}{RAZ} {GRAS}{libelle}{RAZ}")
            if detail:
                for ligne in str(detail).splitlines():
                    print(f"        {ROUGE}{ligne}{RAZ}")
        return bool(condition)

    def egal(self, libelle: str, obtenu: Any, attendu: Any) -> bool:
        """Vérification d'égalité : affiche obtenu/attendu en cas d'échec."""
        return self.verifie(libelle, obtenu == attendu,
                            f"attendu : {attendu!r}\nobtenu  : {obtenu!r}")

    def info(self, texte: str) -> None:
        print(f"  {GRIS}·{RAZ} {GRIS}{texte}{RAZ}")

    def ignore(self, libelle: str, raison: str) -> None:
        """Vérification non exécutée (matériel absent, dépendance manquante)."""
        self.ignores.append(libelle)
        print(f"  {JAUNE}{_SAUT}{RAZ} {libelle} {GRIS}(ignoré : {raison}){RAZ}")

    def conclure(self) -> None:
        """Affiche le bilan et termine le script (code 0 si tout passe, 1 sinon)."""
        total = self.reussites + len(self.echecs)
        duree = time.monotonic() - self._debut
        print()
        print(_TRAIT * 70)
        if self.echecs:
            couleur, verdict = ROUGE, "ÉCHEC" if _UNICODE else "ECHEC"
        else:
            couleur, verdict = VERT, "SUCCÈS" if _UNICODE else "SUCCES"
        resume = (f"{total} vérification(s) : {self.reussites} OK, "
                  f"{len(self.echecs)} échec(s)")
        if self.ignores:
            resume += f", {len(self.ignores)} ignorée(s)"
        print(f"{GRAS}{couleur}{verdict}{RAZ} — {resume} en {duree:.1f}s")
        if self.echecs:
            print(f"\n{ROUGE}Vérifications en échec :{RAZ}")
            for libelle in self.echecs:
                print(f"  - {libelle}")
        print()
        raise SystemExit(1 if self.echecs else 0)


# --- Doublures audio ----------------------------------------------------

class Appel:
    """Un appel enregistré à la doublure audio (lecture ou enregistrement)."""

    def __init__(self, chemin: Path, device: Optional[str] = None,
                 timeout_sec: Optional[float] = None,
                 max_duration_sec: Optional[int] = None,
                 resultat: str = "completed") -> None:
        self.chemin = Path(chemin)
        self.device = device
        self.timeout_sec = timeout_sec
        self.max_duration_sec = max_duration_sec
        self.resultat = resultat
        # Horodatages monotones : permettent de mesurer un intervalle entre
        # deux appels (cadence de la sonnerie) et de savoir si l'appel est
        # terminé (fin is None -> lecture encore en cours).
        self.debut: Optional[float] = None
        self.fin: Optional[float] = None

    @property
    def nom(self) -> str:
        return self.chemin.name

    def __repr__(self) -> str:
        return f"<Appel {self.nom} {self.resultat}>"


class AudioFactice:
    """Doublure de audio_io.play/record : journalise, ne lance aucun processus.

    La lecture dure `duree_lecture` secondes de temps simulé, en interrogeant
    should_continue() comme le fait le vrai audio_io.play : un raccroché
    pendant ce laps de temps produit bien un « interrupted ».

    L'enregistrement écrit un vrai fichier WAV (silence) pour que les
    contrôles de livre_dor.py qui lisent le fichier produit
    (`path.exists()`, `wav_duration_sec()`) travaillent sur un fichier réel.
    """

    def __init__(self, duree_lecture: float = DUREE_LECTURE_SEC,
                 duree_enregistrement: float = 0.2,
                 secondes_enregistrees: float = 5.0,
                 ecrire_fichier: bool = True) -> None:
        self.duree_lecture = duree_lecture
        self.duree_enregistrement = duree_enregistrement
        self.secondes_enregistrees = secondes_enregistrees
        self.ecrire_fichier = ecrire_fichier
        self.lectures: List[Appel] = []
        self.enregistrements: List[Appel] = []
        self._verrou = threading.Lock()

    # signature identique à audio_io.play
    def play(self, path, should_continue, poll_interval: float = 0.1,
             timeout_sec: Optional[float] = None,
             device: Optional[str] = None) -> str:
        appel = Appel(path, device=device, timeout_sec=timeout_sec)
        appel.debut = time.monotonic()
        with self._verrou:
            self.lectures.append(appel)
        echeance = appel.debut + self.duree_lecture
        try:
            while time.monotonic() < echeance:
                if not should_continue():
                    appel.resultat = "interrupted"
                    return "interrupted"
                time.sleep(0.02)
            return "completed"
        finally:
            appel.fin = time.monotonic()

    # signature identique à audio_io.record
    def record(self, path, max_duration_sec: int, should_continue,
               poll_interval: float = 0.1) -> str:
        appel = Appel(path, max_duration_sec=max_duration_sec)
        appel.debut = time.monotonic()
        with self._verrou:
            self.enregistrements.append(appel)
        if self.ecrire_fichier:
            ecrire_wav(Path(path), secondes=self.secondes_enregistrees)
        echeance = appel.debut + self.duree_enregistrement
        try:
            while time.monotonic() < echeance:
                if not should_continue():
                    appel.resultat = "interrupted"
                    return "interrupted"
                time.sleep(0.02)
            return "completed"
        finally:
            appel.fin = time.monotonic()

    # --- Observations ---------------------------------------------------

    def noms_lus(self) -> List[str]:
        with self._verrou:
            return [a.nom for a in self.lectures]

    def noms_enregistres(self) -> List[str]:
        with self._verrou:
            return [a.nom for a in self.enregistrements]

    def noms_interrompus(self) -> List[str]:
        with self._verrou:
            return [a.nom for a in self.lectures + self.enregistrements
                    if a.resultat == "interrupted"]

    def a_lu(self, nom: str) -> bool:
        return nom in self.noms_lus()

    def lectures_de(self, nom: str) -> List[Appel]:
        """Tous les appels de lecture portant sur ce nom de fichier."""
        with self._verrou:
            return [a for a in self.lectures if a.nom == nom]

    def attendre_nb_lectures(self, nom: str, nombre: int, timeout: float = 3.0) -> bool:
        """Attend que le fichier nommé ait été joué au moins `nombre` fois."""
        echeance = time.monotonic() + timeout
        while time.monotonic() < echeance:
            if len(self.lectures_de(nom)) >= nombre:
                return True
            time.sleep(0.02)
        return False

    def attendre_fin(self, nom: str, timeout: float = 3.0) -> bool:
        """Attend la fin de la (première) lecture du fichier nommé."""
        echeance = time.monotonic() + timeout
        while time.monotonic() < echeance:
            appels = self.lectures_de(nom)
            if appels and appels[0].fin is not None:
                return True
            time.sleep(0.02)
        return False

    def reinitialiser(self) -> None:
        with self._verrou:
            self.lectures.clear()
            self.enregistrements.clear()


# --- Sous-processus factice (supervision de audio_io, §7.2) -------------

class ProcessusFactice:
    """Imite subprocess.Popen pour tester la boucle de surveillance d'audio_io.

    Le « processus » se termine tout seul au bout de `duree` secondes avec le
    code `returncode`, et note s'il a reçu un terminate()/kill().
    """

    def __init__(self, duree: float = 0.3, returncode: int = 0,
                 stderr: bytes = b"") -> None:
        self._debut = time.monotonic()
        self._duree = duree
        self._code_final = returncode
        self.returncode: Optional[int] = None
        self.stderr = io.BytesIO(stderr)
        self.terminate_appele = False
        self.kill_appele = False

    def poll(self) -> Optional[int]:
        if self.returncode is None and (time.monotonic() - self._debut) >= self._duree:
            self.returncode = self._code_final
        return self.returncode

    def terminate(self) -> None:
        self.terminate_appele = True
        self.returncode = -15

    def kill(self) -> None:
        self.kill_appele = True
        self.returncode = -9

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        return self.returncode


class PopenFactice:
    """Remplaçant de subprocess.Popen dans audio_io : journalise les commandes.

        with remplacer(audio_io.subprocess, "Popen", PopenFactice()) as _:
            ...
    """

    def __init__(self, duree: float = 0.3, returncode: int = 0,
                 stderr: bytes = b"", erreur: Optional[Exception] = None) -> None:
        self.duree = duree
        self.returncode = returncode
        self.stderr = stderr
        self.erreur = erreur
        self.commandes: List[List[str]] = []
        self.processus: List[ProcessusFactice] = []

    def __call__(self, cmd, **kwargs) -> ProcessusFactice:
        self.commandes.append(list(cmd))
        if self.erreur is not None:
            raise self.erreur
        proc = ProcessusFactice(self.duree, self.returncode, self.stderr)
        self.processus.append(proc)
        return proc

    @property
    def derniere_commande(self) -> List[str]:
        return self.commandes[-1] if self.commandes else []


# --- Fichiers WAV factices ----------------------------------------------

def ecrire_wav(chemin: Path, secondes: float = 0.2, frequence: int = 44100) -> Path:
    """Écrit un vrai WAV mono 16 bits (silence) de la durée demandée."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(chemin), "wb") as fichier:
        fichier.setnchannels(1)
        fichier.setsampwidth(2)
        fichier.setframerate(frequence)
        fichier.writeframes(b"\x00\x00" * int(frequence * secondes))
    return chemin


# --- Remplacement temporaire d'un attribut ------------------------------

@contextlib.contextmanager
def remplacer(objet: Any, nom: str, valeur: Any):
    """Remplace objet.nom le temps du bloc, puis restaure l'original."""
    ancien = getattr(objet, nom)
    setattr(objet, nom, valeur)
    try:
        yield ancien
    finally:
        setattr(objet, nom, ancien)


@contextlib.contextmanager
def journal_status():
    """Capture toutes les écritures de status_io.write_status pendant le bloc.

    Renvoie une liste de tuples (etat, detail), dans l'ordre. Indispensable
    pour vérifier un état traversé rapidement : lire status.json après coup
    ne montre que le dernier état, pas celui qu'on cherchait.
    """
    ecritures: List[tuple] = []
    original = status_io.write_status

    def espion(etat, detail=None):
        ecritures.append((etat, detail))
        return original(etat, detail)

    status_io.write_status = espion
    try:
        yield ecritures
    finally:
        status_io.write_status = original


# --- Banc d'essai : téléphone simulé + arborescence temporaire ----------

# Paramètres de config.py sauvegardés/restaurés autour de chaque scénario.
_PARAMS_SAUVEGARDES = (
    "AUDIO_DIR", "MESSAGES_DIR", "STATUS_FILE", "MODE_CONFIG_FILE",
    "RING_TRIGGER_FILE", "TONALITE_WAV", "BIP_WAV", "RING_OUT_WAV",
    "MESSAGE_GENERIQUE_WAV", "AUCUN_MESSAGE_WAV",
    "RING_INTERVAL_SEC", "RING_ANSWER_GRACE_SEC", "MAX_RECORD_SEC",
    "STATUS_HEARTBEAT_SEC", "SHORT_RECORDING_THRESHOLD_SEC",
    "RECORDING_HANGUP_CONFIRM_SEC", "RESTITUTION_INTERDIGIT_SEC",
    "RESTITUTION_DIGITS_MAX", "RESTITUTION_SOUND_CARD", "MODE_RELOAD_SEC",
    "DISK_WARNING_MB", "DISK_CRITICAL_MB", "AUDIO_PLAY_TIMEOUT_SEC",
    "WATCHDOG_STALE_AFTER_SEC", "SOUND_CARD",
)

# Horodatage de référence des enregistrements d'invités factices.
_BASE_HORODATAGE = datetime.datetime(2026, 6, 20, 14, 0, 0)


class Banc:
    """Un téléphone complet simulé, isolé dans un dossier temporaire.

        with Banc() as banc:
            banc.decrocher()
            banc.composer(3)
            banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT)

    Paramètres :
      restitution      mode restitution (§5.7) plutôt que mode mariage ;
      messages         nombre d'enregistrements d'invités déjà présents ;
      chiffres_maries  chiffres pour lesquels message_N.wav existe ;
      sonnerie         True pour créer ring_out.wav ;
      machine          False pour n'avoir que l'arborescence et les entrées,
                       sans démarrer la machine à états (tests de fonctions
                       pures) ;
      doublure_audio   False pour laisser audio_io.play/record intacts, quand
                       c'est audio_io lui-même qui est sous test.
    Tout autre mot-clé est appliqué à config (ex. RING_INTERVAL_SEC=1).
    """

    def __init__(self, restitution: bool = False, messages: int = 0,
                 chiffres_maries: Iterable[int] = range(10),
                 generique: bool = True, aucun_message: bool = False,
                 sonnerie: bool = True, machine: bool = True,
                 doublure_audio: bool = True,
                 audio: Optional[AudioFactice] = None, **parametres: Any) -> None:
        self.restitution = restitution
        self.nb_messages = messages
        self.chiffres_maries = list(chiffres_maries)
        self.generique = generique
        self.aucun_message = aucun_message
        self.sonnerie = sonnerie
        self.avec_machine = machine
        self.doublure_audio = doublure_audio
        self.audio = audio or AudioFactice()
        self.parametres = parametres
        self.inputs = gpio_io.PhoneInputs()
        self.machine: Optional[livre_dor.GuestBookStateMachine] = None
        self.noms_messages: List[str] = []

    # --- Cycle de vie ---------------------------------------------------

    def __enter__(self) -> "Banc":
        self._dossier = Path(tempfile.mkdtemp(prefix="livredor_test_"))
        self._sauvegarde: Dict[str, Any] = {
            nom: getattr(config, nom) for nom in _PARAMS_SAUVEGARDES
        }
        self._play, self._record = audio_io.play, audio_io.record

        config.AUDIO_DIR = self._dossier / "audio"
        config.MESSAGES_DIR = self._dossier / "messages"
        config.STATUS_FILE = self._dossier / "status.json"
        config.MODE_CONFIG_FILE = self._dossier / "mode_config.json"
        config.RING_TRIGGER_FILE = self._dossier / "ring_trigger"
        config.TONALITE_WAV = config.AUDIO_DIR / "tonalite.wav"
        config.BIP_WAV = config.AUDIO_DIR / "bip.wav"
        config.RING_OUT_WAV = config.AUDIO_DIR / "ring_out.wav"
        config.MESSAGE_GENERIQUE_WAV = config.AUDIO_DIR / "message_generique.wav"
        config.AUCUN_MESSAGE_WAV = config.AUDIO_DIR / "aucun_message.wav"
        # Par défaut la sonnerie périodique ne part jamais : un scénario qui ne
        # l'étudie pas n'a pas à en déclencher une au bout de RING_INTERVAL_SEC.
        config.RING_INTERVAL_SEC = 10 ** 9
        for nom, valeur in self.parametres.items():
            if nom not in _PARAMS_SAUVEGARDES:
                raise ValueError(f"Paramètre de config non sauvegardé : {nom}")
            setattr(config, nom, valeur)

        config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        config.MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
        ecrire_wav(config.TONALITE_WAV, secondes=1.0)
        ecrire_wav(config.BIP_WAV)
        if self.sonnerie:
            ecrire_wav(config.RING_OUT_WAV)
        if self.generique:
            ecrire_wav(config.MESSAGE_GENERIQUE_WAV)
        if self.aucun_message:
            ecrire_wav(config.AUCUN_MESSAGE_WAV)
        for chiffre in self.chiffres_maries:
            ecrire_wav(config.message_wav(chiffre))

        # Enregistrements d'invités créés dans le désordre : la numérotation
        # 1..N doit venir du tri de recorded_messages(), pas de l'ordre de
        # création (§5.7).
        self.noms_messages = [
            (_BASE_HORODATAGE + datetime.timedelta(minutes=i)).strftime(
                "message_%Y-%m-%d_%H-%M-%S.wav")
            for i in range(self.nb_messages)
        ]
        for nom in reversed(self.noms_messages):
            ecrire_wav(config.MESSAGES_DIR / nom)

        if self.doublure_audio:
            audio_io.play = self.audio.play
            audio_io.record = self.audio.record
        mode_io.write_mode(self.restitution)

        if self.avec_machine:
            self.machine = livre_dor.GuestBookStateMachine(self.inputs)
            self._thread = threading.Thread(target=self.machine.run_forever, daemon=True)
            self._thread.start()
            time.sleep(STABILISATION_SEC)
        return self

    def __exit__(self, *exc_info) -> None:
        if self.machine is not None:
            # Raccrocher d'abord : une communication en cours doit se terminer
            # avant l'arrêt coopératif, qui n'est testé qu'en état d'attente.
            self.inputs.set_hook(False)
            time.sleep(STABILISATION_SEC)
            self.machine.request_stop()
            self._thread.join(timeout=5.0)
        audio_io.play, audio_io.record = self._play, self._record
        for nom, valeur in self._sauvegarde.items():
            setattr(config, nom, valeur)
        mode_io.invalidate_cache()
        shutil.rmtree(self._dossier, ignore_errors=True)

    # --- Pilotage du téléphone simulé -----------------------------------

    def decrocher(self, stabiliser: bool = True) -> None:
        self.inputs.set_hook(True)
        if stabiliser:
            time.sleep(STABILISATION_SEC)

    def raccrocher(self, stabiliser: bool = True) -> None:
        self.inputs.set_hook(False)
        if stabiliser:
            time.sleep(STABILISATION_SEC)

    def composer(self, *chiffres: int, pause: float = 0.05) -> None:
        """Compose des chiffres au cadran : off-normal, impulsions, retour au repos.

        Séquence identique à celle du vrai cadran (§1.2) : 10 impulsions
        valent le chiffre 0.
        """
        for chiffre in chiffres:
            nb_impulsions = 10 if chiffre == 0 else chiffre
            self.inputs.set_dial_active(True)
            for _ in range(nb_impulsions):
                time.sleep(PULSE_GAP_SEC)
                self.inputs.register_pulse()
            time.sleep(PULSE_GAP_SEC)
            self.inputs.set_dial_active(False)
            time.sleep(pause)

    def declencher_sonnerie_a_distance(self) -> None:
        """Crée le fichier drapeau ring_trigger, comme le fait le dashboard (§5.1)."""
        config.RING_TRIGGER_FILE.write_text("", encoding="utf-8")

    def patienter(self, secondes: float = STABILISATION_SEC) -> None:
        time.sleep(secondes)

    # --- Observations ---------------------------------------------------

    @property
    def etat(self) -> str:
        return self.machine.state if self.machine else livre_dor.STATE_ATTENTE

    def attendre_etat(self, etat: str, timeout: float = 3.0) -> bool:
        """Attend que la machine atteigne `etat` ; False si le délai expire."""
        echeance = time.monotonic() + timeout
        while time.monotonic() < echeance:
            if self.etat == etat:
                return True
            time.sleep(0.02)
        return False

    def attendre_lecture(self, nom: str, timeout: float = 3.0) -> bool:
        """Attend qu'un fichier donné soit passé à la doublure de lecture."""
        echeance = time.monotonic() + timeout
        while time.monotonic() < echeance:
            if self.audio.a_lu(nom):
                return True
            time.sleep(0.02)
        return False

    def status(self) -> Optional[dict]:
        return status_io.read_status()

    def enregistrements_crees(self) -> List[Path]:
        """Fichiers WAV présents dans le messages/ temporaire, hors préexistants."""
        existants = set(self.noms_messages)
        return sorted(p for p in config.MESSAGES_DIR.glob("*.wav")
                      if p.name not in existants)

    def messages_invites_lus(self) -> List[str]:
        """Noms des seuls enregistrements d'invités joués (hors tonalité, bip, annonces)."""
        attendus = set(self.noms_messages)
        return [nom for nom in self.audio.noms_lus() if nom in attendus]


# --- Signal d'un contact d'impulsions usé -------------------------------

def segments_cadran_use(impulsions: int, fermeture: float = 0.066,
                        repos: float = 0.042, coupure_longue: float = 0.0106,
                        rang_coupure: int = 2, graine: int = 7) -> List[tuple]:
    """Segments (durée, actif) d'un cadran dont le contact d'impulsions est usé.

    Reproduit ce qu'une capture réelle montre sur un tel contact (§7.6) : le
    contact grésille pendant toute la fermeture — des dizaines de fronts, des
    micro-coupures de quelques dixièmes de milliseconde — alors que le repos
    entre deux impulsions, lui, reste franc. Une des fermetures porte en plus
    une coupure longue : c'est elle qui fait compter une impulsion de trop
    quand on filtre avec un simple seuil symétrique, et que le repos exigé par
    gpio_io.FiltreContact doit absorber.

    Les durées par défaut sont celles mesurées sur le téléphone : fermeture
    ~66 ms, repos ~42 ms, coupure parasite de 10,6 ms.
    """
    alea = random.Random(graine)
    segments: List[tuple] = []
    for rang in range(impulsions):
        restant = fermeture
        while restant > 0.002:
            tenu = min(restant, alea.uniform(0.002, 0.012))
            segments.append((tenu, True))
            restant -= tenu
            if restant > 0.002:
                coupure = min(restant, alea.uniform(0.0002, 0.0015))
                segments.append((coupure, False))
                restant -= coupure
        if rang == rang_coupure and coupure_longue > 0:
            segments.append((coupure_longue, False))
            segments.append((fermeture / 3, True))
        segments.append((repos * alea.uniform(0.9, 1.1), False))
    return segments


def echantillonner(segments: Iterable[tuple], frequence: float) -> List[tuple]:
    """Déroule des segments (durée, actif) en échantillons (instant, actif)."""
    pas = 1.0 / frequence
    echantillons: List[tuple] = []
    instant = 0.0
    for duree, actif in segments:
        fin = instant + duree
        while instant < fin:
            echantillons.append((instant, actif))
            instant += pas
    return echantillons


# --- Aides pour les tests sur matériel réel (--reel) --------------------

def gpio_disponible() -> bool:
    return gpio_io.GPIO is not None


def carte_son_disponible() -> bool:
    return audio_io.sound_card_available()


def exiger_gpio(rapport: Rapport) -> gpio_io.PhoneInputs:
    """Initialise les GPIO réels et retourne PhoneInputs ; sort si indisponible."""
    if not gpio_disponible():
        print(f"\n{JAUNE}RPi.GPIO indisponible : le mode --reel doit être lancé "
              f"sur le Raspberry Pi câblé.{RAZ}\n")
        raise SystemExit(2)
    inputs = gpio_io.PhoneInputs()
    gpio_io.setup(inputs)
    rapport.info(f"GPIO initialisés : crochet={config.HOOK_PIN} "
                 f"(actif {config.HOOK_ACTIVE_STATE}), "
                 f"off-normal={config.DIAL_OFFNORMAL_PIN}, "
                 f"impulsions={config.DIAL_PULSE_PIN}")
    return inputs


def exiger_carte_son(rapport: Rapport) -> None:
    """Sort si la carte son configurée n'est pas énumérée par aplay -l (§7.2)."""
    if not carte_son_disponible():
        print(f"\n{JAUNE}Carte son {config.SOUND_CARD} introuvable (aplay -l) : "
              f"le mode --reel demande la carte son branchée.{RAZ}\n")
        raise SystemExit(2)
    rapport.info(f"Carte son détectée : {config.SOUND_CARD}")


def attendre_condition(condition: Callable[[], bool], consigne: str,
                       timeout: float = 30.0) -> Optional[float]:
    """Invite l'opérateur à agir et attend que `condition` soit vraie.

    Retourne le délai écoulé en secondes, ou None si le temps imparti expire.
    """
    print(f"\n  {GRAS}>>> {consigne}{RAZ} {GRIS}({timeout:.0f}s max){RAZ}")
    debut = time.monotonic()
    while (time.monotonic() - debut) < timeout:
        if condition():
            ecoule = time.monotonic() - debut
            print(f"  {GRIS}détecté après {ecoule:.2f}s{RAZ}")
            return ecoule
        time.sleep(0.02)
    print(f"  {ROUGE}rien détecté en {timeout:.0f}s{RAZ}")
    return None


def provenance(nom: str) -> str:
    """D'où vient la valeur effective d'un paramètre de config.py.

    Reprend l'ordre de précédence documenté en tête de config.py : variable
    d'environnement (systemd), puis custom_config.json (page /settings du
    dashboard), puis valeur par défaut du code.
    """
    if nom in os.environ:
        return "variable d'environnement"
    if nom in getattr(config, "_CUSTOM_VALUES", {}):
        return "custom_config.json (dashboard)"
    return "valeur par défaut"


def resume_parametres(rapport: "Rapport", noms: Iterable[str]) -> None:
    """Affiche les paramètres réellement en vigueur et leur origine.

    Les tests sur matériel n'inventent aucun réglage : ils utilisent ceux de
    l'installation (broches GPIO, carte son, durées...), tels que config.py
    les a résolus. Les afficher permet de valider le téléphone *tel qu'il est
    configuré*, et de repérer tout de suite un réglage inattendu.
    """
    rapport.section("Paramètres en vigueur (config.py)")
    for nom in noms:
        rapport.info(f"{nom} = {getattr(config, nom)!r}   [{provenance(nom)}]")


def indice_materiel() -> None:
    """Signale qu'un test physique est possible quand le matériel est présent."""
    if gpio_disponible():
        print(f"\n{JAUNE}Matériel détecté (RPi.GPIO disponible) : relancez avec "
              f"--reel pour tester physiquement le téléphone.{RAZ}")


def confirmer(question: str) -> bool:
    """Question oui/non à l'opérateur, pour les vérifications à l'oreille (§7.6).

    Sans réponse explicitement positive, la vérification est considérée en
    échec : un test d'écoute qu'on valide par mégarde ne vaut rien.
    """
    try:
        reponse = input(f"  {GRAS}?{RAZ} {question} [o/N] ").strip().lower()
    except EOFError:
        return False
    return reponse in ("o", "oui", "y", "yes")


def configurer_logs(verbeux: bool = False) -> None:
    """Silence les logs du service pendant les tests, sauf en mode --verbeux.

    Plusieurs vérifications provoquent volontairement une erreur (arecord
    absent, disque illisible, espace critique) : les traces correspondantes
    sont attendues et brouilleraient le rapport.
    """
    niveau = logging.DEBUG if verbeux else logging.CRITICAL
    logging.basicConfig(level=niveau, format="%(levelname)s %(message)s")
    logging.getLogger().setLevel(niveau)


def parseur(description: str, reel: bool = True):
    """Analyseur d'arguments commun aux scripts de tests/."""
    import argparse
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    if reel:
        parser.add_argument(
            "--reel", action="store_true",
            help="Test sur le matériel réel (Raspberry Pi câblé / carte son) "
                 "au lieu de la simulation.")
    parser.add_argument(
        "--verbeux", action="store_true",
        help="Affiche aussi les logs du service (logging), utile pour "
             "diagnostiquer une vérification en échec.")
    return parser
