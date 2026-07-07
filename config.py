"""Configuration centralisée du livre d'or téléphonique.

Toutes les valeurs par défaut correspondent au tableau récapitulatif
(§9 de docs/specification_livre_dor_telephonique.md). Elles peuvent être
surchargées par des variables d'environnement du même nom pour l'installation
sur le Raspberry Pi final, sans toucher au code.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Arborescence (§8) -------------------------------------------------

AUDIO_SRC_DIR = BASE_DIR / "audio_src"   # fichiers sources bruts, non pré-traités
AUDIO_DIR = BASE_DIR / "audio"           # fichiers panés/gainés, prêts à être joués
MESSAGES_DIR = BASE_DIR / "messages"     # enregistrements des invités
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"

STATUS_FILE = BASE_DIR / "status.json"
RING_TRIGGER_FILE = BASE_DIR / "ring_trigger"
RCLONE_CONFIG_FILE = BASE_DIR / "rclone_config.json"
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


def message_wav(digit: int) -> Path:
    """Chemin du fichier message associé à un chiffre du cadran (0-9)."""
    return AUDIO_DIR / f"message_{digit}.wav"


# --- Audio / ALSA (§4.1, §9) --------------------------------------------

SOUND_CARD = _env("SOUND_CARD", "plughw:1,0")

# --- GPIO (§3, §9) -------------------------------------------------------

HOOK_PIN = _env_int("HOOK_PIN", 17)
DIAL_OFFNORMAL_PIN = _env_int("DIAL_OFFNORMAL_PIN", 27)
DIAL_PULSE_PIN = _env_int("DIAL_PULSE_PIN", 22)

# État logique GPIO correspondant à "décroché" / "cadran en mouvement" : à
# confirmer au multimètre avant câblage définitif (voir checklist §7.6).
HOOK_ACTIVE_STATE = _env("HOOK_ACTIVE_STATE", "LOW")
OFFNORMAL_ACTIF_LEVEL = _env("OFFNORMAL_ACTIF_LEVEL", "LOW")

# Anti-rebond logiciel (§7.2) : fenêtres typiques crochet ~50-100 ms,
# impulsions du cadran nettement plus courtes (impulsion ~60 ms).
HOOK_DEBOUNCE_SEC = _env_float("HOOK_DEBOUNCE_SEC", 0.075)
DIAL_DEBOUNCE_SEC = _env_float("DIAL_DEBOUNCE_SEC", 0.02)

# --- Comportement du parcours invité (§1.2, §9) --------------------------

RING_INTERVAL_SEC = _env_int("RING_INTERVAL_SEC", 90)
RING_ANSWER_GRACE_SEC = _env_int("RING_ANSWER_GRACE_SEC", 5)
MAX_RECORD_SEC = _env_int("MAX_RECORD_SEC", 120)

# --- Dashboard web (§5.2, §9) ---------------------------------------------

WEB_PORT = _env_int("WEB_PORT", 5000)
WEB_PORT_MAX_ATTEMPTS = _env_int("WEB_PORT_MAX_ATTEMPTS", 1)
USE_MDNS = _env("USE_MDNS", "True") == "True"
MDNS_HOSTNAME = _env("MDNS_HOSTNAME", "livredor")
QR_LABEL_SIZE_MM = _env_int("QR_LABEL_SIZE_MM", 45)

# --- Réseau WiFi/AP (§5.3, §9) ---------------------------------------------

AP_CONNECTION_NAME = _env("AP_CONNECTION_NAME", "GuestbookAP")
AP_SSID = _env("AP_SSID", "Livre-dor-Mariage")
AP_IP = _env("AP_IP", "192.168.4.1")
CONNECT_TIMEOUT_SEC = _env_int("CONNECT_TIMEOUT_SEC", 15)
WIFI_SIGNAL_MIN = _env_int("WIFI_SIGNAL_MIN", 25)
WIFI_FAIL_THRESHOLD = _env_int("WIFI_FAIL_THRESHOLD", 3)
WIFI_BLACKLIST_MIN = _env_int("WIFI_BLACKLIST_MIN", 10)

# --- Synchronisation Google Drive (§5.4, §9) -------------------------------

RCLONE_REMOTE = _env("RCLONE_REMOTE", "gdrive")
RCLONE_FOLDER = _env("RCLONE_FOLDER", "MariageGuestBook")
RCLONE_INTERVAL_MIN = _env_int("RCLONE_INTERVAL_MIN", 5)

# --- Stockage (§7.3, §9) ---------------------------------------------------

DISK_WARNING_MB = _env_int("DISK_WARNING_MB", 500)
DISK_CRITICAL_MB = _env_int("DISK_CRITICAL_MB", 100)


def ensure_directories() -> None:
    """Crée les dossiers de l'arborescence s'ils n'existent pas encore."""
    for directory in (AUDIO_SRC_DIR, AUDIO_DIR, MESSAGES_DIR, LOGS_DIR, STATIC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
