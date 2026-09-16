"""Lecture/écriture du mode de fonctionnement (mode_config.json, §5.7, §8).

Contrat : { "restitution": false }

Deux modes s'excluent :

- mode **mariage** (défaut) : le parcours invité nominal (§1.2) — tonalité,
  un chiffre au cadran, message des mariés, bip, puis enregistrement ;
- mode **restitution** (après l'événement, §5.7) : on compose le numéro d'un
  message déjà enregistré et on l'écoute ; ni sonnerie, ni enregistrement.

Le fichier est écrit par le dashboard (§5.2) et relu à chaud par
livre_dor.py à chaque tour de la boucle d'attente : la bascule ne demande ni
accès SSH ni redémarrage du service. Toute lecture tolère l'absence ou la
corruption du fichier (§7.4) : on retombe sur les valeurs par défaut plutôt
que de lever une exception dans la machine à états.
"""

import json
import logging
import os

import config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"restitution": config.MODE_RESTITUTION}


def read_mode() -> dict:
    """Contenu de mode_config.json, complété par les défauts ; ne lève jamais (§7.4)."""
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


def is_restitution() -> bool:
    """True si le mode restitution est actif (§5.7).

    Appelé à chaque tour de la boucle d'attente (~20 fois par seconde) : la
    lecture d'un fichier de quelques dizaines d'octets servi par le page cache
    est négligeable sur un Pi Zero 2 W, et relire systématiquement garantit
    qu'une bascule depuis le dashboard est vue en moins d'une seconde, sans
    mécanisme d'invalidation de cache à maintenir.
    """
    return bool(read_mode().get("restitution", False))


def write_mode(restitution: bool) -> None:
    """Écrit mode_config.json de façon atomique (fichier temporaire + os.replace).

    Même garantie que status_io.write_status : la machine à états ne peut
    jamais lire un JSON à moitié écrit.
    """
    data = {"restitution": bool(restitution)}
    tmp_path = config.MODE_CONFIG_FILE.with_suffix(config.MODE_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, config.MODE_CONFIG_FILE)


def ensure_config_exists() -> None:
    """Crée mode_config.json avec les valeurs par défaut s'il est absent."""
    if not config.MODE_CONFIG_FILE.exists():
        write_mode(DEFAULT_CONFIG["restitution"])


def mode_label() -> str:
    """Libellé du mode courant, pour les logs et le dashboard."""
    return "restitution" if is_restitution() else "mariage"
