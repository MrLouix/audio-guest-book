"""Authentification par mot de passe unique du dashboard (§6).

Un seul mot de passe partagé (pas de gestion d'utilisateurs), haché via
werkzeug.security (PBKDF2) et stocké dans dashboard_config.json — jamais en
clair dans le code. La clé de session Flask est générée aléatoirement au
premier démarrage et persistée dans secret_key.txt (permissions 600), pour
que les sessions ouvertes survivent à un redémarrage du service.
"""

import json
import logging
import os
import secrets
import time
from typing import Dict, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

import config

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "livredor"

# client_id -> (nombre d'échecs consécutifs, horodatage du dernier échec)
_failed_attempts: Dict[str, Tuple[int, float]] = {}


def _read_config() -> dict:
    try:
        return json.loads(config.DASHBOARD_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config(data: dict) -> None:
    tmp_path = config.DASHBOARD_CONFIG_FILE.with_suffix(config.DASHBOARD_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, config.DASHBOARD_CONFIG_FILE)


def ensure_password_configured() -> None:
    """Initialise dashboard_config.json avec le mot de passe par défaut s'il n'existe pas encore."""
    if "password_hash" not in _read_config():
        logger.warning(
            "Aucun mot de passe dashboard configuré : mot de passe par défaut %r utilisé — "
            "à changer immédiatement avec `python3 src/set_password.py`.", DEFAULT_PASSWORD,
        )
        set_password(DEFAULT_PASSWORD)


def set_password(new_password: str) -> None:
    data = _read_config()
    data["password_hash"] = generate_password_hash(new_password)
    _write_config(data)


def verify_password(password: str) -> bool:
    password_hash = _read_config().get("password_hash")
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def get_or_create_secret_key() -> str:
    """Lit secret_key.txt, ou en génère une nouvelle (permissions 600) au premier démarrage."""
    path = config.SECRET_KEY_FILE
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.exception("Impossible de restreindre les permissions de %s à 600", path)
    return key


def _delay_for_count(count: int) -> float:
    if count < config.LOGIN_FAILED_ATTEMPTS_THRESHOLD:
        return 0.0
    steps_over = count - config.LOGIN_FAILED_ATTEMPTS_THRESHOLD + 1
    return min(config.LOGIN_FAILED_DELAY_SEC * steps_over, config.LOGIN_FAILED_DELAY_MAX_SEC)


def throttle_delay(client_id: str) -> float:
    """Délai (s) à appliquer avant de traiter une tentative de connexion de client_id (§6, anti-brute-force)."""
    count, _ = _failed_attempts.get(client_id, (0, 0.0))
    return _delay_for_count(count)


def register_failed_attempt(client_id: str) -> int:
    """Enregistre un échec ; retourne le nombre total d'échecs consécutifs pour ce client."""
    count, _ = _failed_attempts.get(client_id, (0, 0.0))
    count += 1
    _failed_attempts[client_id] = (count, time.monotonic())
    return count


def register_success(client_id: str) -> None:
    _failed_attempts.pop(client_id, None)
