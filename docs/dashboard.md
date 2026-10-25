# Dashboard web

[← README](../README.md) · Spécification : §5.2 (dashboard), §6 (mot de passe)

```bash
python3 src/dashboard_app.py   # http://0.0.0.0:5000/ ; en production : dashboard.service
```

Adresse stable : **`http://livredor.local:5000/`**, valable en WiFi comme en point d'accès (mDNS).

## Les pages

| Page | À quoi elle sert |
|---|---|
| `/` | État du téléphone en direct (code couleur), nombre de messages, mode, réseau + IP, derniers logs, bouton « Sonner maintenant » |
| `/messages` | Tri des messages après l'événement : voir [Après l'événement](apres_evenement.md#trier-les-messages) |
| `/settings` | Choix des fichiers audio et paramètres de fonctionnement (ci-dessous) |
| `/mode` | Bascule mariage / restitution : voir [Après l'événement](apres_evenement.md#mode-restitution) |
| `/qr`, `/qr/label` | QR codes du dashboard et du point d'accès, étiquette imprimable |
| `/wifi` | Connecter le Pi au WiFi du lieu en photographiant son QR code |
| `/rclone` | Synchronisation Google Drive : voir [Réseau, synchro et services](reseau_synchro.md#synchronisation-google-drive) |

Accueil : statut rafraîchi toutes les ~4 s, logs toutes les ~8 s (défilement préservé). API : `/api/status`, `/api/logs` (150 dernières lignes), `/api/messages/count`, `/api/ring` (POST, crée `ring_trigger`). Un fichier absent (`status.json`, logs) ne provoque jamais d'erreur 500.

Le port est fixe (`WEB_PORT=5000`) : s'il est occupé, le service s'arrête avec un message explicite plutôt que de changer de port ; le port utilisé est écrit dans `active_port.txt`.

## Fichiers audio (page Paramètres)

Section **« 🎵 Fichiers audio »** : une liste déroulante par rôle (sonnerie, message générique, message 0 à 9, annonce « aucun message »), alimentée par le contenu de `audio_src/`. Enregistrer écrit le choix dans `audio_config.json` **et convertit aussitôt** les seuls rôles modifiés. Un badge indique par rôle s'il est converti, absent, ou si sa source a changé depuis (typiquement après une synchro Drive). « Tout reconvertir » régénère l'ensemble. Détails de la conversion : [Audio](audio.md#fichiers-audio--audio_src-et-audio).

API (écritures réservées à l'administrateur) : `GET /api/audio/sources`, `POST /api/audio/roles`, `POST /api/audio/reconvert`, `POST /api/audio/test` (joue un fichier sur sa sortie, pour vérifier le câblage).

Garde-fous : conversions refusées (409) tant que le téléphone n'est pas au repos, car décoder une dizaine de fichiers sature le Pi Zero 2 W ; un verrou répond « occupé » plutôt que d'empiler deux conversions ; les noms de fichier reçus sont validés (ni chemin absolu, ni `..`, et le fichier doit exister dans `audio_src/`).

## Paramètres de fonctionnement

Les paramètres modifiables (`MODIFIABLE_PARAMS` dans `src/config.py`) sont écrits dans `custom_config.json` et répartis en trois sections :

| Section | Qui peut modifier | Prise en compte | Paramètres |
|---|---|---|---|
| 🔊 Réglages courants | tout utilisateur connecté | à chaud | `VOLUME_SONNERIE`, `VOLUME_COMBINE`, `MICRO_GAIN_DB`, `RING_INTERVAL_SEC`, `RING_COUNT`, `RING_PAUSE_SEC` |
| 🛠️ Réglages administrateur | administrateur | à chaud | niveaux maximum (`AUDIO_MAX_DB_CASQUE`, `AUDIO_MAX_DB_LINEOUT`), fenêtre de grâce, durées d'enregistrement et de lecture, tonalité, sorties du codec, mode restitution |
| ⚠️ Redémarrage nécessaire | administrateur | au redémarrage du service | `SOUND_CARD`, niveaux et anti-rebonds GPIO, `LOG_LEVEL` |

« À chaud » : la machine à états relit `custom_config.json` (au plus une fois par seconde) entre deux communications, jamais au milieu d'une. Un non-administrateur voit les sections admin en lecture seule ; les valeurs hors bornes sont refusées.

- **Volumes** : `VOLUME_SONNERIE` et `VOLUME_COMBINE` sont des pourcentages (0-100) du niveau maximum de la sortie utilisée ; 0 coupe la sortie. Appliqués à chaque commutation, donc par rôle.
- **Volume de prise du micro** : `MICRO_GAIN_DB` (0 à 42 dB, défaut 24), gain d'entrée du jack MIC, réparti par `audio-setup.sh` entre le préampli (pas de 6 dB) et le PGA (pas de 1,5 dB), réappliqué avant chaque enregistrement s'il a changé. Le traitement normalise ensuite le niveau : le baisser ne rend pas les messages plus faibles, le monter fait saturer les voix fortes.
- **Niveaux maximum** : `AUDIO_MAX_DB_CASQUE` (−57 à +6 dB, défaut +6) et `AUDIO_MAX_DB_LINEOUT` (−48 à +15 dB, défaut 0) fixent ce que vaut 100 %. Le pourcentage est linéaire en dB entre le plancher du contrôle et ce maximum : à 50 %, un line out plafonné à 0 dB sort à −24 dB. Échelles à confirmer sur le Pi avec `amixer -c 1 cget numid=7` (casque) et `numid=8` (line out).

Le formulaire des fichiers audio est distinct de celui des paramètres : changer un délai ne déclenche pas de conversion. Liste complète des paramètres : [guide d'installation §4](guide_installation.md#4-configuration-9).

## Mots de passe

Toutes les pages et `/api/*` exigent une session, sauf `/login` et les fichiers statiques.

```bash
python3 src/set_password.py         # mot de passe standard (défaut « livredor », à changer)
python3 src/set_admin_password.py   # mot de passe administrateur, sans valeur par défaut
```

- **Mot de passe standard** : tant qu'il n'a pas été changé, `livredor` fonctionne et un avertissement est journalisé à chaque démarrage. Il donne accès à la supervision, aux volumes, à la sonnerie et au tri des messages.
- **Mot de passe administrateur** : seul autorisé à modifier les paramètres « admin », à choisir, reconvertir et tester les fichiers audio, et à réinitialiser la synchronisation bidirectionnelle. Tant que `set_admin_password.py` n'a pas été lancé, il n'existe pas.
- Les deux sont indépendants et stockés hachés (PBKDF2) dans `dashboard_config.json`. La clé de session (`secret_key.txt`, permissions 600) est persistée : les sessions survivent aux redémarrages.
- `/login` redirige vers la page demandée (protégé contre l'open-redirect) ; `/logout` ferme la session. Après 3 échecs, chaque tentative est temporisée. Session valable `SESSION_LIFETIME_HOURS` (défaut 12 h).

⚠️ Le serveur reste en HTTP local, sans TLS : le mot de passe protège de la curiosité des invités du même réseau, pas d'un attaquant motivé ; c'est le niveau voulu (§6).

## QR codes et WiFi

- **`/qr`** : QR de l'URL du dashboard (`http://livredor.local:5000/`, jamais une IP tant que `USE_MDNS=True`), plus un QR WiFi du point d'accès, affiché seulement quand le Pi est en mode AP.
- **`/qr/label`** : étiquette imprimable dimensionnée en millimètres (`?taille=NN`, défaut `QR_LABEL_SIZE_MM`, entre 10 et 200 mm), sur fond blanc fixe.
- **`/wifi`** : photographier le QR WiFi du lieu pour y connecter le Pi. Décodage **dans le navigateur** avec `static/jsQR.min.js` (vendorisé, aucune requête réseau), confirmation du SSID, repli sur une saisie manuelle sans caméra. ⚠️ En mode AP, la confirmation coupe la connexion en cours (une seule antenne).
- **`/api/wifi/add`** (POST) : crée le profil et connecte via `nmcli` ; les erreurs reviennent en JSON, jamais de crash.
