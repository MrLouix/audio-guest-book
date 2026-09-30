"""CLI pour définir/changer le mot de passe admin du dashboard (§6).

Ce mot de passe fonctionne en parallèle du mot de passe standard défini par
set_password.py, mais ouvre une session administrateur : seule celle-ci peut
modifier les paramètres réservés, les fichiers audio et réinitialiser la
synchronisation bidirectionnelle. Il n'a aucune valeur par défaut — tant que
ce script n'a pas été exécuté, personne n'est administrateur.

Usage :
    python3 src/set_admin_password.py
"""

import auth


def main() -> None:
    auth.saisir_mot_de_passe("Nouveau mot de passe admin : ", auth.set_admin_password,
                             "Mot de passe admin défini.")


if __name__ == "__main__":
    main()
