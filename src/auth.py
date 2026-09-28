"""Authentification par mot de passe unique du dashboard (§6).

Un seul mot de passe partagé (pas de gestion d'utilisateurs), haché via
werkzeug.security (PBKDF2) et stocké dans dashboard_config.json — jamais en
clair dans le code. La clé de session Flask est générée aléatoirement au
premier démarrage et persistée dans secret_key.txt (permissions 600), pour
que les sessions ouvertes survivent à un redémarrage du service.

Un second mot de passe, dit « admin », ouvre une session administrateur :
elle seule peut modifier les paramètres de niveau « admin », les fichiers
audio et réinitialiser la synchronisation bidirectionnelle (§6).
Contrairement au mot de passe standard, il n'a aucune valeur par défaut —
tant que `set_admin_password.py` n'a pas été exécuté, seul le mot de passe
standard fonctionne, sans accès administrateur.
"""

import getpass
import logging
import os
import secrets
import sys
import time
from typing import Callable, Dict, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

import config
import fichiers

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "livredor"

# client_id -> (nombre d'échecs consécutifs, horodatage du dernier échec)
_failed_attempts: Dict[str, Tuple[int, float]] = {}


PASSWORD_KEY = "password_hash"
ADMIN_PASSWORD_KEY = "admin_password_hash"


def _read_config() -> dict:
    return fichiers.lire_json_dict(config.DASHBOARD_CONFIG_FILE)


def _set_hash(key: str, new_password: str) -> None:
    data = _read_config()
    data[key] = generate_password_hash(new_password)
    fichiers.ecrire_json_atomique(config.DASHBOARD_CONFIG_FILE, data)


def _verify(key: str, password: str) -> bool:
    """False si aucun mot de passe n'est enregistré sous `key`."""
    password_hash = _read_config().get(key)
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def ensure_password_configured() -> None:
    """Initialise dashboard_config.json avec le mot de passe par défaut s'il n'existe pas encore."""
    if PASSWORD_KEY not in _read_config():
        logger.warning(
            "Aucun mot de passe dashboard configuré : mot de passe par défaut %r utilisé — "
            "à changer immédiatement avec `python3 src/set_password.py`.", DEFAULT_PASSWORD,
        )
        set_password(DEFAULT_PASSWORD)


def set_password(new_password: str) -> None:
    _set_hash(PASSWORD_KEY, new_password)


def verify_password(password: str) -> bool:
    return _verify(PASSWORD_KEY, password)


def set_admin_password(new_password: str) -> None:
    _set_hash(ADMIN_PASSWORD_KEY, new_password)


def verify_admin_password(password: str) -> bool:
    """Non configuré par défaut : renvoie False tant que set_admin_password.py n'a pas été exécuté."""
    return _verify(ADMIN_PASSWORD_KEY, password)


def saisir_mot_de_passe(invite: str, enregistrer: Callable[[str], None],
                        confirmation: str) -> None:
    """Saisie interactive commune à set_password.py et set_admin_password.py.

    Demande le mot de passe deux fois, refuse un mot de passe vide ou une
    confirmation différente (sortie en code 1), puis appelle `enregistrer`.
    """
    password = getpass.getpass(invite)
    confirm = getpass.getpass("Confirmez : ")
    if not password:
        print("Le mot de passe ne peut pas être vide.", file=sys.stderr)
        raise SystemExit(1)
    if password != confirm:
        print("Les deux mots de passe ne correspondent pas.", file=sys.stderr)
        raise SystemExit(1)
    enregistrer(password)
    print(confirmation)


def get_or_create_secret_key() -> str:
    """Lit secret_key.txt, ou en génère une nouvelle (permissions 600) au premier démarrage."""
    path = config.SECRET_KEY_FILE
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_hex(32)
    # Créé directement en 600 : écrire puis restreindre laissait la clé
    # lisible par tous le temps de l'écriture.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    try:
        # Fichier préexistant (vide) : os.open n'a pas changé ses droits.
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
