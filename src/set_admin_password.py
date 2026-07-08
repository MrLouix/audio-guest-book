"""CLI pour définir/changer le mot de passe admin du dashboard.

Ce mot de passe fonctionne en parallèle du mot de passe standard défini par
set_password.py : les deux donnent accès à la même session. Il n'a aucune
valeur par défaut — tant que ce script n'a pas été exécuté, seul le mot de
passe standard fonctionne. Aucune fonctionnalité admin n'est encore
conditionnée dessus, c'est pour l'instant un second mot de passe en
parallèle.

Usage :
    python3 src/set_admin_password.py
"""

import getpass
import sys

import auth


def main() -> None:
    password = getpass.getpass("Nouveau mot de passe admin : ")
    confirm = getpass.getpass("Confirmez : ")
    if not password:
        print("Le mot de passe ne peut pas être vide.", file=sys.stderr)
        raise SystemExit(1)
    if password != confirm:
        print("Les deux mots de passe ne correspondent pas.", file=sys.stderr)
        raise SystemExit(1)
    auth.set_admin_password(password)
    print("Mot de passe admin défini.")


if __name__ == "__main__":
    main()
