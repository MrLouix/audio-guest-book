# Sécurité

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir d'issue publique pour une faille de sécurité.
Utilisez le signalement privé de GitHub : onglet **Security** du dépôt, puis
**Report a vulnerability**. Décrivez le problème, les étapes pour le
reproduire et la version (commit) concernée.

Vous recevrez une réponse dès que possible ; le projet étant maintenu à titre
personnel, aucun délai n'est garanti.

## Versions suivies

Seule la dernière version de la branche par défaut reçoit des correctifs.

## Périmètre

Le téléphone est prévu pour un réseau local de confiance (WiFi de la salle ou
point d'accès du Pi). Sont notamment dans le périmètre :

- le dashboard web (authentification, sessions, téléversement de fichiers,
  paramètres) ;
- le provisioning WiFi et la bascule en point d'accès ;
- la synchronisation Google Drive (`rclone`) et le stockage de ses
  identifiants.

Bonnes pratiques de déploiement : changez les mots de passe du dashboard dès
l'installation (`src/set_password.py`, `src/set_admin_password.py`), et ne
versionnez jamais `secret_key.txt`, `dashboard_config.json` ni
`rclone_config.json` (ils sont déjà dans `.gitignore`).
