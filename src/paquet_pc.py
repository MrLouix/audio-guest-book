"""Paquet de la page de tri pour PC : un seul fichier, tri_messages.pyz.

Pour trier les messages sur un PC sans copier tout le dépôt : une archive
Python exécutable (zipapp) qui ne contient que ce dont tri_messages.py a
besoin — sept modules, le gabarit de la page, son JS et son CSS. Le dashboard
du Pi la sert en téléchargement sur la page /messages.

Sur le PC, il suffit de Python 3 et de `python -m pip install flask numpy`,
puis de poser tri_messages.pyz dans le dossier qui contient les deux dossiers
synchronisés par Google Drive pour ordinateur, et de le lancer (double-clic
sous Windows, ou `python tri_messages.pyz`).

Usage :
    python3 src/paquet_pc.py                      # écrit ./tri_messages.pyz
    python3 src/paquet_pc.py chemin/vers/tri_messages.pyz
"""

import argparse
import io
import zipfile
from pathlib import Path
from typing import BinaryIO, Union

import config

NOM = "tri_messages.pyz"

# Modules de src/ nécessaires à tri_messages.py, dépendances comprises.
MODULES = (
    "config.py", "fichiers.py", "traitement_audio.py", "score_message.py",
    "messages_index.py", "messages_page.py", "tri_messages.py",
)
RESSOURCES = ("templates/messages.html", "static/dashboard.css", "static/messages.js")

# Point d'entrée de l'archive. Lancée d'un double-clic, la fenêtre se
# fermerait aussitôt sur une erreur : le message reste affiché jusqu'à Entrée.
MAIN = '''"""Tri des messages du livre d'or téléphonique, sur PC (voir tri_messages.py)."""
import sys


def _arret(message):
    print(message)
    try:
        input("\\nAppuyez sur Entrée pour fermer.")
    except EOFError:
        pass
    sys.exit(1)


try:
    import flask  # noqa: F401
    import numpy  # noqa: F401
except ImportError as exc:
    _arret(f"Module Python manquant : {exc.name}.\\n"
           "Installez-le une fois pour toutes avec :\\n"
           "    python -m pip install flask numpy")

import tri_messages

try:
    tri_messages.main()
except SystemExit as exc:
    if exc.code not in (None, 0):
        _arret(str(exc.code))
    raise
'''


def construire(destination: Union[Path, BinaryIO]) -> None:
    """Écrit l'archive dans `destination` (chemin ou fichier binaire ouvert)."""
    src = config.BASE_DIR / "src"
    if isinstance(destination, (str, Path)):
        with open(destination, "wb") as f:
            construire(f)
        return
    # La ligne #! rend l'archive exécutable directement sous Linux et macOS ;
    # Python ignore ce qui précède les données zip.
    destination.write(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__main__.py", MAIN)
        for nom in MODULES:
            zf.write(src / nom, nom)
        for nom in RESSOURCES:
            zf.write(config.BASE_DIR / nom, nom)


def en_memoire() -> bytes:
    tampon = io.BytesIO()
    construire(tampon)
    return tampon.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("destination", nargs="?", default=NOM)
    args = parser.parse_args()
    construire(Path(args.destination))
    print(f"Écrit : {args.destination}")


if __name__ == "__main__":
    main()
