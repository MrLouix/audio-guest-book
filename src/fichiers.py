"""Accès fichiers partagés par tous les modules : JSON, écriture atomique, journaux.

Chaque fichier d'état du projet (status.json, mode_config.json,
custom_config.json, audio_config.json, rclone_config.json,
dashboard_config.json) est écrit par un processus et lu par un autre. Deux
règles valent pour tous, et ne sont donc écrites qu'ici :

- **écriture atomique** (fichier temporaire + os.replace) : un lecteur ne voit
  jamais un fichier à moitié écrit (§7.2) ;
- **lecture tolérante** : un fichier absent, illisible ou édité à la main de
  travers renvoie une valeur par défaut, jamais une exception (§7.4).

Ce module n'importe pas config.py : config.py s'en sert lui-même.
"""

import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any, Dict, List

FORMAT_JOURNAL = "%(asctime)s %(levelname)s %(message)s"

# Rotation des journaux applicatifs (§7.3) : 5 x 1 Mo.
JOURNAL_TAILLE_MAX = 1_000_000
JOURNAL_NB_ARCHIVES = 5


def ecrire_texte_atomique(path: Path, contenu: str) -> None:
    """Écrit `contenu` dans `path` via `<nom>.tmp` + os.replace ; lève OSError.

    Le fichier temporaire est supprimé si l'écriture échoue : il ne traîne
    jamais à côté du fichier d'état.
    """
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        tmp_path.write_text(contenu, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def ecrire_json_atomique(path: Path, data: Any, indent: int = 2) -> None:
    """Sérialise `data` en JSON (UTF-8 lisible) et l'écrit atomiquement ; lève OSError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ecrire_texte_atomique(path, json.dumps(data, ensure_ascii=False, indent=indent))


def lire_json_dict(path: Path) -> Dict[str, Any]:
    """Objet JSON contenu dans `path` ; {} s'il est absent, illisible ou n'est pas un objet."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def fichiers_wav(dossier: Path) -> List[Path]:
    """Fichiers .wav (extension insensible à la casse) directement dans `dossier`.

    Sous-dossiers exclus (messages/brut/), liste vide si le dossier est absent
    ou illisible. Ordre non garanti : à chaque appelant de trier selon son
    besoin.
    """
    try:
        entrees = list(dossier.iterdir())
    except OSError:
        return []
    return [p for p in entrees if p.suffix.lower() == ".wav" and p.is_file()]


def dernieres_lignes(path: Path, n: int, octets_max: int = 64 * 1024) -> List[str]:
    """Les n dernières lignes d'un fichier texte ; liste vide si absent ou illisible.

    Seule la fin du fichier est lue (`octets_max`), jamais le fichier entier :
    le dashboard relit les journaux toutes les quelques secondes sur un Pi
    Zero, et un journal non tourné peut grossir sans limite.
    """
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            debut = max(0, f.tell() - octets_max)
            f.seek(debut)
            brut = f.read()
    except OSError:
        return []
    lignes = brut.decode("utf-8", errors="replace").splitlines()
    # Lecture démarrée en plein fichier : la première ligne est presque
    # toujours coupée en son milieu, on l'écarte.
    if debut > 0 and lignes:
        lignes = lignes[1:]
    return lignes[-n:] if n > 0 else []


def configurer_journal(fichier: Path, niveau: int) -> None:
    """Journal racine : console + fichier tournant (5 x 1 Mo), même format partout (§7.3)."""
    fichier.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT_JOURNAL)
    racine = logging.getLogger()
    racine.setLevel(niveau)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    racine.addHandler(console)

    fichier_handler = logging.handlers.RotatingFileHandler(
        fichier, maxBytes=JOURNAL_TAILLE_MAX, backupCount=JOURNAL_NB_ARCHIVES,
        encoding="utf-8")
    fichier_handler.setFormatter(formatter)
    racine.addHandler(fichier_handler)
