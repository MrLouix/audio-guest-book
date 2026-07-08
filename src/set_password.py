"""CLI pour définir/changer le mot de passe du dashboard (§6).

Usage :
    python3 src/set_password.py
"""

import getpass
import sys

import auth


def main() -> None:
    password = getpass.getpass("Nouveau mot de passe du dashboard : ")
    confirm = getpass.getpass("Confirmez : ")
    if not password:
        print("Le mot de passe ne peut pas être vide.", file=sys.stderr)
        raise SystemExit(1)
    if password != confirm:
        print("Les deux mots de passe ne correspondent pas.", file=sys.stderr)
        raise SystemExit(1)
    auth.set_password(password)
    print("Mot de passe du dashboard mis à jour.")


if __name__ == "__main__":
    main()
