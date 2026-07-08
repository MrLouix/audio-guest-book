"""Lecture/écriture atomique de status.json (§7.2, §8).

Contrat : { "etat": "...", "derniere_maj": "ISO-8601", "detail": "texte optionnel" }
Écrit par livre_dor.py, lu par le dashboard (Sprint 5) — jamais de JSON à
moitié écrit grâce à l'écriture via fichier temporaire + os.replace().
"""

import datetime
import json
import logging
import os
from typing import Optional

import config

logger = logging.getLogger(__name__)


def write_status(etat: str, detail: Optional[str] = None) -> None:
    """Écrit status.json de façon atomique (fichier temporaire + os.replace)."""
    data = {
        "etat": etat,
        "derniere_maj": datetime.datetime.now().isoformat(timespec="seconds"),
        "detail": detail or "",
    }
    tmp_path = config.STATUS_FILE.with_suffix(config.STATUS_FILE.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, config.STATUS_FILE)
    except OSError:
        logger.exception("Impossible d'écrire %s", config.STATUS_FILE)


def read_status() -> Optional[dict]:
    """Lit status.json ; retourne None si absent ou illisible (tolérance requise §7.4)."""
    try:
        return json.loads(config.STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
