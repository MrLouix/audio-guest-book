"""Configuration centralisée du livre d'or téléphonique.

Toutes les valeurs par défaut correspondent au tableau récapitulatif
(§9 de docs/specification_livre_dor_telephonique.md). Elles peuvent être
surchargées par des variables d'environnement du même nom pour l'installation
sur le Raspberry Pi final, sans toucher au code.

Ordre de précédence d'un paramètre, du plus fort au plus faible :

1. la variable d'environnement du même nom (systemd `Environment=`) — le
   réglage figé de l'installation ;
2. custom_config.json, écrit par la page /settings du dashboard, pour les
   paramètres déclarés dans MODIFIABLE_PARAMS ;
3. la valeur par défaut inscrite ici.

Tout est résolu une fois pour toutes à l'import de ce module, et livre_dor.py
tourne dans un autre processus que le dashboard : une modification faite
depuis /settings ne prend donc effet qu'au redémarrage du service (cf.
RESTART_REQUIRED_PARAMS). Seul le mode mariage/restitution bascule à chaud,
via mode_config.json et non par ce mécanisme (§5.7).
"""

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# config.py vit dans src/ ; l'arborescence de données (audio/, messages/,
# logs/, static/, templates/...) reste à la racine du projet (§8).
BASE_DIR = Path(__file__).resolve().parent.parent

# Fichier de configuration personnalisée pour les paramètres modifiables via le dashboard
CUSTOM_CONFIG_FILE = BASE_DIR / "custom_config.json"


def _read_custom_config() -> Dict[str, Any]:
    """Valeurs enregistrées depuis la page /settings ; {} si absent ou illisible (§7.4)."""
    try:
        data = json.loads(CUSTOM_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# Lu une seule fois, à l'import : ce module fournit des constantes.
_CUSTOM_VALUES = _read_custom_config()


def _param(name: str, default: Any, convert: Callable[[Any], Any]) -> Any:
    """Résout un paramètre selon l'ordre de précédence documenté en tête de module.

    La variable d'environnement est analysée strictement : une valeur invalide
    doit faire échouer le démarrage plutôt que passer inaperçue. Une valeur
    venue de custom_config.json est au contraire tolérante — le fichier est
    écrit par le dashboard et peut avoir été édité à la main, il ne doit jamais
    empêcher le service de démarrer (§7.4) ; on retombe alors sur le défaut.
    """
    if name in os.environ:
        return convert(os.environ[name])
    if name in _CUSTOM_VALUES:
        try:
            return convert(_CUSTOM_VALUES[name])
        except (TypeError, ValueError):
            pass
    return convert(default)


def _env(name: str, default: str) -> str:
    return _param(name, default, str)


def _env_int(name: str, default: int) -> int:
    return _param(name, default, int)


def _env_float(name: str, default: float) -> float:
    return _param(name, default, float)


# --- Arborescence (§8) -------------------------------------------------

AUDIO_SRC_DIR = BASE_DIR / "audio_src"   # fichiers sources bruts, non pré-traités
AUDIO_DIR = BASE_DIR / "audio"           # fichiers panés/gainés, prêts à être joués
MESSAGES_DIR = BASE_DIR / "messages"     # enregistrements des invités
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"

STATUS_FILE = BASE_DIR / "status.json"
RING_TRIGGER_FILE = BASE_DIR / "ring_trigger"
RCLONE_CONFIG_FILE = BASE_DIR / "rclone_config.json"
MODE_CONFIG_FILE = BASE_DIR / "mode_config.json"
DASHBOARD_CONFIG_FILE = BASE_DIR / "dashboard_config.json"
SECRET_KEY_FILE = BASE_DIR / "secret_key.txt"
ACTIVE_PORT_FILE = BASE_DIR / "active_port.txt"

LIVRE_DOR_LOG = LOGS_DIR / "livre_dor.log"
RESEAU_LOG = LOGS_DIR / "reseau.log"
RCLONE_LOG = LOGS_DIR / "rclone.log"

# --- Fichiers audio générés (§4.2, §8) ----------------------------------

TONALITE_WAV = AUDIO_DIR / "tonalite.wav"
BIP_WAV = AUDIO_DIR / "bip.wav"
RING_OUT_WAV = AUDIO_DIR / "ring_out.wav"
MESSAGE_GENERIQUE_WAV = AUDIO_DIR / "message_generique.wav"
AUCUN_MESSAGE_WAV = AUDIO_DIR / "aucun_message.wav"


def message_wav(digit: int) -> Path:
    """Chemin du fichier message associé à un chiffre du cadran (0-9)."""
    return AUDIO_DIR / f"message_{digit}.wav"


# --- Audio / ALSA (§4.1, §9) --------------------------------------------

SOUND_CARD = _env("SOUND_CARD", "plughw:1,0")

# Filet de sécurité contre un sous-processus aplay bloqué (§7.2).
AUDIO_PLAY_TIMEOUT_SEC = _env_int("AUDIO_PLAY_TIMEOUT_SEC", 180)

# Intervalle de rafraîchissement de status.json en état attente, pour que le
# watchdog (§7.1) ne le voie jamais périmé lors des longues idles.
#
# Chaque écriture est atomique (fichier temporaire + os.replace), donc coûte un
# bloc neuf sur la carte SD : mesuré à ~13 Ko de trafic réel pour 88 octets de
# contenu, soit ~36 Mo/jour à 30 s. À 120 s on descend à ~9 Mo/jour sans
# rapprocher dangereusement du seuil du watchdog — WATCHDOG_STALE_AFTER_SEC
# vaut 300 s, il reste donc 2,5 battements de marge.
STATUS_HEARTBEAT_SEC = _env_int("STATUS_HEARTBEAT_SEC", 120)

# Durée en-dessous de laquelle un enregistrement est jugé "très court" :
# conservé (jamais supprimé) mais marqué/logué (§7.2).
SHORT_RECORDING_THRESHOLD_SEC = _env_float("SHORT_RECORDING_THRESHOLD_SEC", 2.0)

# Anti-rebond spécifique au raccroché pendant un enregistrement en cours,
# pour ignorer une micro-coupure du crochet (faux contact) sans tronquer le
# message (§7.2).
RECORDING_HANGUP_CONFIRM_SEC = _env_float("RECORDING_HANGUP_CONFIRM_SEC", 0.1)

# --- GPIO (§3, §9) -------------------------------------------------------

HOOK_PIN = _env_int("HOOK_PIN", 17)
DIAL_OFFNORMAL_PIN = _env_int("DIAL_OFFNORMAL_PIN", 27)
DIAL_PULSE_PIN = _env_int("DIAL_PULSE_PIN", 22)

# État logique GPIO correspondant à "décroché" / "cadran en mouvement" : à
# confirmer au multimètre avant câblage définitif (voir checklist §7.6).
HOOK_ACTIVE_STATE = _env("HOOK_ACTIVE_STATE", "LOW")
OFFNORMAL_ACTIF_LEVEL = _env("OFFNORMAL_ACTIF_LEVEL", "LOW")

# Niveau de la broche d'impulsions *pendant* une impulsion. Il ne suit pas
# forcément celui du contact off-normal : beaucoup de cadrans ont un contact
# d'impulsions « normalement fermé », donc une broche au niveau bas au repos
# (pull-up tiré à la masse) qui remonte à chaque impulsion — PULSE_ACTIF_LEVEL
# vaut alors HIGH alors que OFFNORMAL_ACTIF_LEVEL reste à LOW. Le défaut suit
# l'off-normal, c'est-à-dire le comportement d'avant ce réglage.
# À vérifier avant toute autre chose : `python3 tests/scope_impulsions.py
# --niveaux`, cadran immobile, doit montrer la broche au niveau *opposé* (§7.6).
PULSE_ACTIF_LEVEL = _env("PULSE_ACTIF_LEVEL", "HIGH")

# Anti-rebond logiciel (§7.2) : fenêtres typiques crochet ~50-100 ms,
# impulsions du cadran nettement plus courtes (impulsion ~60 ms).
HOOK_DEBOUNCE_SEC = _env_float("HOOK_DEBOUNCE_SEC", 0.075)
DIAL_DEBOUNCE_SEC = _env_float("DIAL_DEBOUNCE_SEC", 0.02)

# Les entrées ne sont pas lues par interruption mais par échantillonnage : un
# thread relit les trois broches à cette cadence et ne retient un changement
# que s'il se maintient (voir gpio_io.FiltreContact). 1 kHz donne une dizaine
# de points sur la plus courte micro-coupure d'un contact usé, largement assez
# pour la trancher.
#
# Cette cadence-là n'est utile que pendant une rotation du cadran. Le reste du
# temps — soit l'essentiel d'une soirée — seul le crochet compte, et sa
# confirmation dure déjà 75 ms : la boucle retombe donc à
# GPIO_ECHANTILLONNAGE_REPOS_HZ, et ne repasse en cadence rapide que pendant
# GPIO_ACTIVITE_SEC après le dernier changement de niveau *brut* d'une broche.
# Le déclencheur est le niveau brut et non l'état filtré : pendant un
# grésillement les fronts se succèdent sans arrêt et relancent la cadence
# rapide en continu, si bien qu'on n'échantillonne jamais lentement au milieu
# d'une impulsion. Le coût de la boucle étant presque entièrement celui du
# réveil du thread, et non du travail fait à chaque tour, baisser la cadence au
# repos divise la consommation d'autant (§7.2).
#
# Régler GPIO_ECHANTILLONNAGE_REPOS_HZ à la même valeur que
# GPIO_ECHANTILLONNAGE_HZ désactive l'adaptation.
# `python3 tests/scope_impulsions.py --charge` mesure le coût réel des deux
# cadences sur la machine de destination.
GPIO_ECHANTILLONNAGE_HZ = _env_float("GPIO_ECHANTILLONNAGE_HZ", 1000.0)
GPIO_ECHANTILLONNAGE_REPOS_HZ = _env_float("GPIO_ECHANTILLONNAGE_REPOS_HZ", 50.0)
GPIO_ACTIVITE_SEC = _env_float("GPIO_ACTIVITE_SEC", 2.0)

# Durées de maintien exigées avant de croire à un changement d'état. Elles
# remplacent le bouncetime de RPi.GPIO, qui ne savait pas exprimer un contact
# usé : sa fenêtre unique bloque *tous* les fronts pendant N ms après un front
# accepté, ce qui ne peut pas à la fois absorber 25 ms de grésillement et
# respecter des impulsions dont la durée varie du simple au triple.
#
# Le contact d'impulsions en demande deux, dissymétriques (§7.6) : sur un
# cadran usé, le contact grésille *pendant* la fermeture alors que le repos
# entre deux impulsions reste franc. On ouvre donc l'impulsion vite, et on ne
# la clôt qu'après un repos franc — plus long que la plus longue micro-coupure
# interne, plus court que le plus court repos réel.
#
#   PULSE_MIN_ACTIF_SEC  <<  plus courte impulsion réelle (~33 ms à 10 imp/s)
#   plus longue micro-coupure  <  PULSE_MIN_REPOS_SEC  <  plus court repos réel
#
# `python3 tests/scope_impulsions.py --reel` mesure ces quatre durées sur le
# cadran et balaie PULSE_MIN_REPOS_SEC : le bon réglage est le centre du palier
# qu'affiche le tableau.
PULSE_MIN_ACTIF_SEC = _env_float("PULSE_MIN_ACTIF_SEC", 0.005)
PULSE_MIN_REPOS_SEC = _env_float("PULSE_MIN_REPOS_SEC", 0.025)

# Le crochet et l'off-normal sont des états, pas des événements : une seule
# durée de confirmation, dans les deux sens. Défauts dérivés des anciens
# anti-rebonds, pour ne rien changer aux installations réglées. Comme
# RESTITUTION_SOUND_CARD, ils ne sont pas dans MODIFIABLE_PARAMS, où une valeur
# figée mentirait dès que le paramètre dont ils héritent change.
HOOK_CONFIRM_SEC = _env_float("HOOK_CONFIRM_SEC", HOOK_DEBOUNCE_SEC)
OFFNORMAL_CONFIRM_SEC = _env_float("OFFNORMAL_CONFIRM_SEC", DIAL_DEBOUNCE_SEC)

# --- Comportement du parcours invité (§1.2, §9) --------------------------

RING_INTERVAL_SEC = _env_int("RING_INTERVAL_SEC", 90)
RING_ANSWER_GRACE_SEC = _env_int("RING_ANSWER_GRACE_SEC", 5)
MAX_RECORD_SEC = _env_int("MAX_RECORD_SEC", 120)

# --- Mode restitution (§5.7) ----------------------------------------------

# Valeur par défaut au premier démarrage uniquement : la source de vérité à
# l'exécution est mode_config.json, éditable à chaud depuis le dashboard (§5.2).
# Motif booléen identique à USE_MDNS, seul motif bool du projet.
MODE_RESTITUTION = _env("MODE_RESTITUTION", "False") == "True"

# Nombre maximal de chiffres du numéro de message : au-delà, la saisie se
# ferme immédiatement sans attendre l'inter-chiffre (§5.7).
RESTITUTION_DIGITS_MAX = _env_int("RESTITUTION_DIGITS_MAX", 4)

# Délai de silence du cadran validant un numéro incomplet (« 1 » puis attente).
# Suspendu pendant que le cadran est en mouvement, pour ne jamais valider un
# numéro au milieu d'un chiffre en cours de composition (§5.7).
RESTITUTION_INTERDIGIT_SEC = _env_float("RESTITUTION_INTERDIGIT_SEC", 3.0)

# Périphérique ALSA de lecture des messages des invités. Par défaut identique à
# SOUND_CARD ; échappatoire si la lecture des enregistrements mono doit être
# routée uniquement vers l'écouteur du combiné (voir §5.7).
RESTITUTION_SOUND_CARD = _env("RESTITUTION_SOUND_CARD", SOUND_CARD)

# Durée de validité du mode en mémoire avant relecture de mode_config.json.
# La machine à états interroge le mode à chaque tour de sa boucle d'attente
# (20 Hz) : relire le fichier à cette cadence est inutile pour une valeur qui
# change deux fois dans la vie de l'appareil. Une seconde reste imperceptible
# à l'usage et divise par vingt le nombre d'appels système (~1,7 million par
# jour, ramenés à ~86 000). Une bascule depuis le dashboard invalide le cache
# du processus qui écrit, et reste donc immédiate côté interface.
MODE_RELOAD_SEC = _env_float("MODE_RELOAD_SEC", 1.0)

# --- Dashboard web (§5.2, §9) ---------------------------------------------

WEB_PORT = _env_int("WEB_PORT", 5000)
WEB_PORT_MAX_ATTEMPTS = _env_int("WEB_PORT_MAX_ATTEMPTS", 1)
USE_MDNS = _env("USE_MDNS", "True") == "True"
MDNS_HOSTNAME = _env("MDNS_HOSTNAME", "livredor")
QR_LABEL_SIZE_MM = _env_int("QR_LABEL_SIZE_MM", 45)

# --- Authentification dashboard (§6) ---------------------------------------

SESSION_LIFETIME_HOURS = _env_int("SESSION_LIFETIME_HOURS", 12)
LOGIN_FAILED_ATTEMPTS_THRESHOLD = _env_int("LOGIN_FAILED_ATTEMPTS_THRESHOLD", 3)
LOGIN_FAILED_DELAY_SEC = _env_float("LOGIN_FAILED_DELAY_SEC", 1.0)
LOGIN_FAILED_DELAY_MAX_SEC = _env_float("LOGIN_FAILED_DELAY_MAX_SEC", 10.0)

# --- Réseau WiFi/AP (§5.3, §9) ---------------------------------------------

AP_CONNECTION_NAME = _env("AP_CONNECTION_NAME", "GuestbookAP")
AP_SSID = _env("AP_SSID", "Livre-dor-Mariage")
AP_IP = _env("AP_IP", "192.168.4.1")
# Mot de passe WPA2 du point d'accès de secours (créé par wifi_or_ap.sh, Sprint 8).
# Non spécifié dans le §9 : un AP ouvert exposerait le dashboard (déjà protégé
# par mot de passe, §6) à quiconque à portée, donc un WPA2 documenté par défaut
# est préférable — à changer à l'installation comme le mot de passe dashboard.
AP_PASSWORD = _env("AP_PASSWORD", "livredormariage")
CONNECT_TIMEOUT_SEC = _env_int("CONNECT_TIMEOUT_SEC", 15)
WIFI_SIGNAL_MIN = _env_int("WIFI_SIGNAL_MIN", 25)
WIFI_FAIL_THRESHOLD = _env_int("WIFI_FAIL_THRESHOLD", 3)
WIFI_BLACKLIST_MIN = _env_int("WIFI_BLACKLIST_MIN", 10)

# --- Synchronisation Google Drive (§5.4, §9) -------------------------------

RCLONE_REMOTE = _env("RCLONE_REMOTE", "gdrive")
RCLONE_FOLDER = _env("RCLONE_FOLDER", "MariageGuestBook")
RCLONE_INTERVAL_MIN = _env_int("RCLONE_INTERVAL_MIN", 5)
RCLONE_TIMEOUT_SEC = _env_int("RCLONE_TIMEOUT_SEC", 120)
RCLONE_DRYRUN_TIMEOUT_SEC = _env_int("RCLONE_DRYRUN_TIMEOUT_SEC", 20)

# Unité systemd régénérée par le dashboard quand l'intervalle change (§5.4) ;
# symlinkée depuis /etc/systemd/system par scripts/setup_rclone_systemd.sh,
# de sorte que le réécrire ne demande aucun privilège particulier.
SYSTEMD_DIR = BASE_DIR / "systemd"
SYSTEMD_RCLONE_TIMER_FILE = SYSTEMD_DIR / "rclone-sync.timer"
RCLONE_SYSTEMD_UNIT = "rclone-sync.timer"

# --- Stockage (§7.3, §9) ---------------------------------------------------

DISK_WARNING_MB = _env_int("DISK_WARNING_MB", 500)
DISK_CRITICAL_MB = _env_int("DISK_CRITICAL_MB", 100)

# --- Supervision systemd / watchdog (§7.1) ---------------------------------

# Au-delà de cet âge (secondes) sans mise à jour de status.json, le
# watchdog considère livre-dor.service gelé et le redémarre.
WATCHDOG_STALE_AFTER_SEC = _env_int("WATCHDOG_STALE_AFTER_SEC", 300)

# --- Transcription batch (§5.5) — optionnelle, hors événement -------------

WHISPER_BINARY = _env("WHISPER_BINARY", "whisper-cli")
WHISPER_MODEL_PATH = _env("WHISPER_MODEL_PATH", str(BASE_DIR / "whisper.cpp" / "models" / "ggml-tiny-q5_0.bin"))
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "fr")
WHISPER_TIMEOUT_SEC = _env_int("WHISPER_TIMEOUT_SEC", 600)


def ensure_directories() -> None:
    """Crée les dossiers de l'arborescence s'ils n'existent pas encore."""
    for directory in (AUDIO_SRC_DIR, AUDIO_DIR, MESSAGES_DIR, LOGS_DIR, STATIC_DIR):
        directory.mkdir(parents=True, exist_ok=True)


# --- Paramètres modifiables via le dashboard ---

# Liste des paramètres modifiables via l'interface /settings
# Format: (nom, type, valeur_par_defaut, description)
MODIFIABLE_PARAMS = {
    "RING_INTERVAL_SEC": {"type": "int", "default": 90, "label": "Intervalle de sonnerie (secondes)"},
    "RING_ANSWER_GRACE_SEC": {"type": "int", "default": 5, "label": "Fenêtre de grâce pour répondre (secondes)"},
    "MAX_RECORD_SEC": {"type": "int", "default": 120, "label": "Durée max d'enregistrement (secondes)"},
    "SHORT_RECORDING_THRESHOLD_SEC": {"type": "float", "default": 2.0, "label": "Seuil enregistrement court (secondes)"},
    "AUDIO_PLAY_TIMEOUT_SEC": {"type": "int", "default": 180, "label": "Timeout lecture audio (secondes)"},
    "SOUND_CARD": {"type": "str", "default": "plughw:1,0", "label": "Carte son ALSA"},
    "HOOK_ACTIVE_STATE": {"type": "str", "default": "LOW", "label": "Niveau actif crochet (LOW/HIGH)"},
    "OFFNORMAL_ACTIF_LEVEL": {"type": "str", "default": "LOW", "label": "Niveau actif cadran (LOW/HIGH)"},
    "PULSE_ACTIF_LEVEL": {"type": "str", "default": "HIGH", "label": "Niveau actif pulse (LOW/HIGH)"},
    "HOOK_DEBOUNCE_SEC": {"type": "float", "default": 0.075, "label": "Anti-rebond crochet (secondes)"},
    "DIAL_DEBOUNCE_SEC": {"type": "float", "default": 0.02, "label": "Anti-rebond cadran (secondes)"},
    # Mode restitution (§5.7). MODE_RESTITUTION n'est pas listé : la bascule a
    # sa propre page /mode et passe par mode_config.json, à chaud.
    # RESTITUTION_SOUND_CARD non plus : c'est un routage ALSA avancé, et son
    # défaut suit SOUND_CARD, ce qu'une valeur figée ici casserait.
    "RESTITUTION_DIGITS_MAX": {"type": "int", "default": 4, "label": "Mode restitution : nombre max de chiffres du numéro"},
    "RESTITUTION_INTERDIGIT_SEC": {"type": "float", "default": 3.0, "label": "Mode restitution : silence du cadran validant le numéro (secondes)"},
}

# Paramètres nécessitant un redémarrage du service après modification.
# C'est le cas de tous : ils sont résolus à l'import de ce module, et
# livre_dor.py tourne dans un autre processus que le dashboard — rien ne
# relit custom_config.json en cours de route. Le champ existait mais ne
# listait que SOUND_CARD, ce qui laissait croire que les autres prenaient
# effet immédiatement.
RESTART_REQUIRED_PARAMS = set(MODIFIABLE_PARAMS)


def _get_current_value(name: str) -> Any:
    """Récupère la valeur actuelle d'un paramètre (d'abord custom_config, puis globale)."""
    import sys
    
    # D'abord vérifier dans custom_config.json
    try:
        if CUSTOM_CONFIG_FILE.exists():
            with CUSTOM_CONFIG_FILE.open("r", encoding="utf-8") as f:
                custom_config = json.load(f)
                if name in custom_config:
                    return custom_config[name]
    except (OSError, json.JSONDecodeError):
        pass
    
    # Puis retourner la valeur globale si elle existe
    # Utiliser sys.modules pour éviter la référence circulaire
    config_module = sys.modules.get(__name__)
    if config_module and hasattr(config_module, name):
        return getattr(config_module, name)
    
    # Enfin, retourner la valeur par défaut depuis MODIFIABLE_PARAMS
    if name in MODIFIABLE_PARAMS:
        return MODIFIABLE_PARAMS[name]["default"]
    
    return None


def get_all_config() -> Dict[str, Any]:
    """Retourne un dictionnaire avec tous les paramètres modifiables et leurs valeurs actuelles."""
    result = {}
    for name, info in MODIFIABLE_PARAMS.items():
        result[name] = _get_current_value(name)
    return result


def get_config_value(name: str) -> Any:
    """Retourne la valeur actuelle d'un paramètre modifiable."""
    return _get_current_value(name)


def update_config(new_values: Dict[str, Any]) -> Dict[str, Any]:
    """Met à jour les paramètres dans custom_config.json.
    
    Args:
        new_values: Dictionnaire {nom_param: nouvelle_valeur}
    
    Returns:
        Dict avec {"ok": bool, "erreur": str ou None, "params_modifies": list, "redemarrage_necessaire": bool}
    """
    # Lire la config existante
    try:
        if CUSTOM_CONFIG_FILE.exists():
            with CUSTOM_CONFIG_FILE.open("r", encoding="utf-8") as f:
                custom_config = json.load(f)
        else:
            custom_config = {}
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "erreur": f"Impossible de lire custom_config.json: {e}", "params_modifies": [], "redemarrage_necessaire": False}
    
    # Valider et appliquer les nouvelles valeurs
    params_modifies = []
    redemarrage_necessaire = False
    
    for name, value in new_values.items():
        if name not in MODIFIABLE_PARAMS:
            continue
        
        param_info = MODIFIABLE_PARAMS[name]
        expected_type = param_info["type"]
        
        # Valider le type
        try:
            if expected_type == "int":
                value = int(value)
            elif expected_type == "float":
                value = float(value)
            elif expected_type == "str":
                value = str(value)
        except (ValueError, TypeError):
            return {"ok": False, "erreur": f"Valeur invalide pour {name}: doit être un {expected_type}", "params_modifies": [], "redemarrage_necessaire": False}
        
        # Vérifier si le paramètre nécessite un redémarrage
        if name in RESTART_REQUIRED_PARAMS:
            redemarrage_necessaire = True
        
        # Mettre à jour
        custom_config[name] = value
        params_modifies.append(name)
    
    # Écrire la nouvelle configuration
    try:
        CUSTOM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CUSTOM_CONFIG_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(custom_config, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, CUSTOM_CONFIG_FILE)
    except OSError as e:
        return {"ok": False, "erreur": f"Impossible d'écrire custom_config.json: {e}", "params_modifies": [], "redemarrage_necessaire": False}
    
    return {
        "ok": True,
        "erreur": None,
        "params_modifies": params_modifies,
        "redemarrage_necessaire": redemarrage_necessaire,
    }


def ensure_custom_config_exists() -> None:
    """Crée custom_config.json avec les valeurs par défaut si inexistant."""
    if not CUSTOM_CONFIG_FILE.exists():
        default_config = {name: info["default"] for name, info in MODIFIABLE_PARAMS.items()}
        CUSTOM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CUSTOM_CONFIG_FILE.write_text(json.dumps(default_config, ensure_ascii=False, indent=2), encoding="utf-8")
