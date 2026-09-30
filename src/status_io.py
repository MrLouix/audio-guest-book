"""Lecture/écriture atomique de status.json (§7.2, §8).

Contrat : { "etat": "...", "derniere_maj": "ISO-8601", "detail": "texte optionnel" }
Écrit par livre_dor.py, lu par le dashboard (Sprint 5) — jamais de JSON à
moitié écrit grâce à l'écriture via fichier temporaire + os.replace().
"""

import datetime
import logging
from typing import Optional

import config
import fichiers

logger = logging.getLogger(__name__)

# Valeurs possibles de « etat » : c'est le contrat entre livre_dor.py, qui
# les écrit, et le dashboard et le watchdog, qui les lisent.
ETAT_ATTENTE = "attente"
ETAT_SONNERIE = "sonnerie"
ETAT_DECROCHE = "decroche"
ETAT_NUMEROTATION = "numerotation"
ETAT_LECTURE_MESSAGE = "lecture_message"
ETAT_APPEL_REPONDU = "appel_repondu"
ETAT_ENREGISTREMENT = "enregistrement"
ETAT_RESTITUTION_NUMEROTATION = "restitution_numerotation"
ETAT_RESTITUTION_LECTURE = "restitution_lecture"
ETAT_ERREUR = "erreur"

# États où personne n'utilise le téléphone : une opération lourde (conversion
# audio) peut y être lancée sans gêner un invité.
ETATS_AU_REPOS = (ETAT_ATTENTE, ETAT_ERREUR)


def write_status(etat: str, detail: Optional[str] = None) -> None:
    """Écrit status.json de façon atomique (fichier temporaire + os.replace)."""
    data = {
        "etat": etat,
        "derniere_maj": datetime.datetime.now().isoformat(timespec="seconds"),
        "detail": detail or "",
    }
    try:
        # Compact (indent=None) : écrit à chaque changement d'état et à chaque
        # battement, sur une carte SD.
        fichiers.ecrire_json_atomique(config.STATUS_FILE, data, indent=None)
    except OSError:
        logger.exception("Impossible d'écrire %s", config.STATUS_FILE)


def read_status() -> Optional[dict]:
    """Lit status.json ; retourne None si absent ou illisible (tolérance requise §7.4)."""
    return fichiers.lire_json_dict(config.STATUS_FILE) or None
