"""Lecture/écriture du mode de fonctionnement (mode_config.json, §5.7, §8).

Contrat : { "restitution": false }

Deux modes s'excluent :

- mode **mariage** (défaut) : le parcours invité nominal (§1.2) — tonalité,
  un chiffre au cadran, message des mariés, bip, puis enregistrement ;
- mode **restitution** (après l'événement, §5.7) : on compose le numéro d'un
  message déjà enregistré et on l'écoute ; ni sonnerie, ni enregistrement.

Le fichier est écrit par le dashboard (§5.2) et relu à chaud par
livre_dor.py : la bascule ne demande ni accès SSH ni redémarrage du service.
Toute lecture tolère l'absence ou la corruption du fichier (§7.4) : on
retombe sur les valeurs par défaut plutôt que de lever une exception dans la
machine à états.

La machine à états interroge le mode à chaque tour de sa boucle d'attente
(20 Hz). La valeur est donc gardée en mémoire pendant config.MODE_RELOAD_SEC
(1 s par défaut) : le fichier n'est relu qu'une fois par seconde, ce qui reste
imperceptible pour une valeur qui change deux fois dans la vie de l'appareil,
et évite ~1,7 million de paires d'appels système par jour sur la carte SD.
write_mode() invalide le cache, de sorte qu'une bascule depuis le dashboard
est reflétée immédiatement par le processus qui l'a écrite.
"""

import json
import logging
import os
import threading
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"restitution": config.MODE_RESTITUTION}

_lock = threading.Lock()
_cached: Optional[dict] = None
_cached_at = 0.0


def _read_file() -> dict:
    """Lecture brute du fichier, complétée par les défauts ; ne lève jamais (§7.4)."""
    try:
        data = json.loads(config.MODE_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        logger.warning("%s ne contient pas un objet JSON, valeurs par défaut utilisées.",
                       config.MODE_CONFIG_FILE)
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def invalidate_cache() -> None:
    """Force la prochaine lecture à repasser par le fichier."""
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


def read_mode(force: bool = False) -> dict:
    """Contenu de mode_config.json, avec un cache de config.MODE_RELOAD_SEC.

    force=True court-circuite le cache, pour les rares points où la valeur doit
    être exacte à l'instant présent plutôt que fraîche à la seconde.
    Une copie est renvoyée : un appelant ne peut pas altérer le cache.
    """
    global _cached, _cached_at
    now = time.monotonic()
    with _lock:
        if (not force and _cached is not None
                and (now - _cached_at) < config.MODE_RELOAD_SEC):
            return dict(_cached)
        data = _read_file()
        _cached = data
        _cached_at = now
        return dict(data)


def is_restitution(force: bool = False) -> bool:
    """True si le mode restitution est actif (§5.7)."""
    return bool(read_mode(force=force).get("restitution", False))


def write_mode(restitution: bool) -> None:
    """Écrit mode_config.json de façon atomique (fichier temporaire + os.replace).

    Même garantie que status_io.write_status : la machine à états ne peut
    jamais lire un JSON à moitié écrit.
    """
    data = {"restitution": bool(restitution)}
    tmp_path = config.MODE_CONFIG_FILE.with_suffix(config.MODE_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, config.MODE_CONFIG_FILE)
    # Le processus qui bascule doit voir sa propre écriture tout de suite : le
    # dashboard réaffiche la page juste après le POST (§5.2).
    invalidate_cache()


def ensure_config_exists() -> None:
    """Crée mode_config.json avec les valeurs par défaut s'il est absent."""
    if not config.MODE_CONFIG_FILE.exists():
        write_mode(DEFAULT_CONFIG["restitution"])


def mode_label() -> str:
    """Libellé du mode courant, pour les logs et le dashboard."""
    return "restitution" if is_restitution() else "mariage"
