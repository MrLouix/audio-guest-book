"""CLI pour définir/changer le mot de passe du dashboard (§6).

Usage :
    python3 src/set_password.py
"""

import auth


def main() -> None:
    auth.saisir_mot_de_passe("Nouveau mot de passe du dashboard : ", auth.set_password,
                             "Mot de passe du dashboard mis à jour.")


if __name__ == "__main__":
    main()
