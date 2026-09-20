# Livre d'or téléphonique (Socotel S63)

Transformation d'un téléphone à cadran vintage Socotel S63 en livre d'or automatique pour un mariage : les invités décrochent, composent un chiffre, écoutent un message des mariés, puis laissent le leur après un bip.

## Documentation

- [`docs/specification_livre_dor_telephonique.md`](docs/specification_livre_dor_telephonique.md) — cahier des charges technique complet (matériel, câblage, architecture logicielle, fiabilité).
- [`docs/dev_plan.md`](docs/dev_plan.md) — plan de développement détaillé en sprints.
- [`docs/guide_installation.md`](docs/guide_installation.md) — guide d'installation reproductible (câblage, dépendances, configuration, premier démarrage).
- [`docs/checklist_mise_en_service.md`](docs/checklist_mise_en_service.md) — checklist de recette à dérouler sur le matériel final avant l'événement.
- [`tests/README.md`](tests/README.md) — tests unitaires par fonction (décroché, raccroché, cadran, lecture, enregistrement, sonnerie...), en simulation ou sur le matériel réel.

## Installation (Sprint 0)

Sur le Raspberry Pi (Raspberry Pi OS Lite / Bookworm) :

```bash
./scripts/install.sh
```

Installe les paquets système (`alsa-utils`, `rclone`, `ffmpeg`, `avahi-daemon`), configure le hostname `livredor` (mDNS), crée un environnement virtuel Python avec les dépendances de `requirements.txt`, vérifie l'arborescence du projet et la présence du Codec Zero, applique `scripts/audio-setup.sh` sous root (`alsactl store`, pour que la carte soit correcte dès le boot) et signale une version de rclone trop ancienne pour `bisync`.

## Gestion de l'environnement virtuel Python

Si le venv est corrompu ou si vous devez le recréer manuellement :

```bash
# Recréer le venv (après avoir installé python3-full)
rm -rf .venv venv
python3 -m venv venv

# Activer le venv
source venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt

# Désactiver le venv (quitter l'environnement)
deactivate
```

Les paramètres (broches GPIO, carte son, seuils réseau, etc.) sont centralisés dans `src/config.py` et surchargeables par variables d'environnement — voir §9 de la spécification.

Le code Python vit dans `src/` ; l'arborescence de données (`audio_src/`, `audio/`, `messages/`, `logs/`, `static/`, `templates/`, `status.json`...) reste à la racine du projet, conformément à l'arborescence de déploiement du §8. Tous les scripts s'exécutent directement (`python3 src/<script>.py`), sans installation du projet ni `PYTHONPATH` à configurer.

## Sorties audio (IQaudio Codec Zero)

Les deux sorties du codec sont câblées à des destinations différentes :

| Sortie du codec | Matériel | Ce qui y passe |
|---|---|---|
| **line out** | 1 haut-parleur **mono** de sonnerie, qui lit la piste **gauche** | `ring_out.wav` |
| **headphone** (stéréo) | écouteur du combiné (**L**) + écouteur secondaire (**R**) | tonalité, messages des mariés, bip, mode restitution |

Le codec ne peut pas alimenter les deux en même temps : il est **commuté avant chaque lecture**, line out pour la sonnerie, casque pour tout le reste. Tous les fichiers joués sont donc **stéréo avec les deux pistes identiques** — aucun panning, contrairement au câblage précédent où les deux destinations se partageaient une seule sortie stéréo.

Tous les réglages du DA7213 (numids `amixer`) vivent dans `scripts/audio-setup.sh`, et nulle part ailleurs : un numid n'est qu'un index dans l'énumération des contrôles ALSA, qui peut glisser d'une version de driver à l'autre. `src/alsa_io.py` ne connaît que trois mots — `lineout`, `headphone`, `both`.

```bash
./scripts/audio-setup.sh status           # état courant (entrée, routage, sorties)
./scripts/audio-setup.sh headphone        # configuration complète, sortie casque
./scripts/audio-setup.sh switch-lineout   # bascule de sortie seule (quelques ms)
python3 src/alsa_io.py status             # même chose, vu depuis Python
```

`livre_dor.py` applique la configuration complète une fois au démarrage, puis ne fait plus que des bascules rapides. Un échec de commutation est journalisé mais n'empêche jamais la lecture : le mauvais haut-parleur vaut mieux que le silence en plein mariage.

## Pré-traitement audio

Deux dossiers, deux rôles :

- **`audio_src/`** — les fichiers sources bruts, tels que déposés (mp3, m4a, wav…), **avec leur nom d'origine**. C'est le seul des deux qui est synchronisé avec Google Drive : on peut y déposer une sonnerie ou un message depuis un smartphone.
- **`audio/`** — les WAV convertis, prêts à être joués. Entièrement généré, jamais synchronisé, jamais édité à la main.

La conversion produit du **48 kHz, 16 bits (S16_LE), stéréo L = R**. 48 kHz parce que c'est la cadence exigée par RNNoise, et parce que le full duplex impose que lecture et capture partagent cadence et format — `arecord` enregistre donc lui aussi en 48 kHz stéréo.

Le fichier qui joue chaque rôle (sonnerie, message générique, message 0 à 9, annonce « aucun message ») se choisit **dans le dashboard**, page Paramètres, par liste déroulante sur le contenu de `audio_src/` — aucun renommage nécessaire. Le choix est enregistré dans `audio_config.json` ; un rôle sans choix explicite retombe sur l'ancienne convention de nommage (`audio_src/sonnerie.*`), ce qui laisse fonctionner une installation antérieure telle quelle.

En ligne de commande :

```bash
python3 src/prepare_audio.py                 # (re)génère tout dans audio/
python3 src/prepare_audio.py --role sonnerie  # un seul rôle
python3 src/prepare_audio.py --play-all       # rejoue chaque fichier sur SA sortie
```

`tonalite.wav` (440+480 Hz) et `bip.wav` (800 Hz, 600 ms, encadré de silence) sont générés par synthèse, sans fichier source. Un rôle sans source est simplement ignoré : `livre_dor.py` retombe sur `message_generique.wav`. Chaque fichier est écrit via un temporaire puis `os.replace()` — une reconversion déclenchée depuis le dashboard ne peut donc pas faire lire un WAV tronqué à `aplay`.

Les primitives de lecture/enregistrement bas niveau (`audio_io.py`) peuvent être testées isolément :

```bash
python3 src/audio_io.py play audio/tonalite.wav
python3 src/audio_io.py record test.wav --duration 5
```

## Machine à états — scénario nominal (Sprint 2)

```bash
python3 src/livre_dor.py --test   # affichage temps réel des 3 GPIO, pour valider le câblage
python3 src/livre_dor.py          # démarre la machine à états (nécessite un vrai Raspberry Pi)
```

Parcours implémenté (§1.2, scénario « appel sortant ») :
attente → décroché (tonalité 440+480 Hz) → dès la première impulsion, la tonalité s'arrête → numérotation (comptage des impulsions, chiffre validé au retour du cadran au repos, 10 impulsions = chiffre 0) → lecture de `audio/message_N.wav` (repli sur `message_generique.wav` si absent) → bip → enregistrement (`messages/message_AAAA-MM-JJ_HH-MM-SS.wav`, jamais d'écrasement) → retour à l'attente. Toute lecture est interrompue immédiatement au raccroché ; l'enregistrement est plafonné à `MAX_RECORD_SEC`.

`gpio_io.py` isole l'accès matériel (RPi.GPIO, callbacks avec anti-rebond `bouncetime`) derrière `PhoneInputs`, un état partagé thread-safe indépendant du matériel — ce qui permet de vérifier toute la logique de la machine à états sans Raspberry Pi (audio et GPIO simulés) avant le déploiement.

## Sonnerie & scénario « appel entrant » (Sprint 3)

En attente, `livre_dor.py` sonne (`audio/ring_out.wav`) toutes les `RING_INTERVAL_SEC` (défaut 90 s), et immédiatement si le dashboard crée le fichier `ring_trigger` (consommé puis supprimé). **`RING_INTERVAL_SEC = 0` coupe la sonnerie périodique** : le téléphone ne sonne plus de lui-même, mais le bouton « Sonner maintenant » du dashboard continue de fonctionner et le parcours invité est inchangé ; `status.json` porte alors le détail « sonnerie périodique désactivée », pour qu'une coupure volontaire ne ressemble pas à une panne. Un décroché pendant la sonnerie ou dans les `RING_ANSWER_GRACE_SEC` (défaut 5 s) qui suivent sa fin simule un vrai appel entrant : la sonnerie est coupée immédiatement, **aucune tonalité n'est jouée et le cadran est ignoré** (impulsions journalisées en debug, sans effet), un message est tiré au hasard parmi tous les `message_N.wav` + `message_generique.wav` disponibles (jamais deux fois de suite le même), puis bip → enregistrement, comme dans le flux nominal. Un décroché hors de cette fenêtre suit le flux nominal habituel (tonalité + cadran). Chaque sonnerie terminée republie l'état `attente` dans `status.json` : le dashboard ne reste jamais affiché sur « sonnerie » alors que le téléphone est déjà revenu au repos.

## Surcouches de fiabilité (Sprint 4)

- **status.json** (`status_io.py`) : écrit de façon atomique (fichier temporaire + `os.replace()`) à chaque transition d'état, contrat `{ "etat", "derniere_maj", "detail" }` (§8) ; rafraîchi périodiquement en attente (`STATUS_HEARTBEAT_SEC`) pour ne jamais paraître périmé.
- **Démarrage robuste** : attente active de la carte son configurée (`SOUND_CARD`, boucle qui ne renonce jamais, état `erreur` affiché en attendant) ; refus de démarrer si `bip.wav` ou `message_generique.wav` sont absents (message explicite + `status.json` en erreur).
- **Espace disque** (`disk_space_state`) : sous `DISK_WARNING_MB` (500 Mo), l'enregistrement est tenté quand même (statut `erreur` signalé) ; sous `DISK_CRITICAL_MB` (100 Mo), l'enregistrement est refusé.
- **Micro-coupures du crochet pendant l'enregistrement** : `HangupConfirmer` exige que le raccroché reste stable au moins `RECORDING_HANGUP_CONFIRM_SEC` avant d'arrêter l'enregistrement, pour ne pas tronquer un message sur un faux contact.
- **Enregistrements très courts** (< `SHORT_RECORDING_THRESHOLD_SEC`) conservés, jamais supprimés, seulement journalisés.
- **Logs** : rotation automatique (`RotatingFileHandler`, 5 × 1 Mo) sur `logs/livre_dor.log`, en plus de la console.
- **Exception globale** : toute exception non prévue dans la machine à états est journalisée (traceback complet), `status.json` bascule en `erreur`, puis l'exception se propage pour que systemd relance le service (Sprint 10) — `GPIO.cleanup()` reste garanti par le bloc `finally`.

## Dashboard web (Sprint 5)

```bash
python3 src/dashboard_app.py   # démarre le serveur sur http://0.0.0.0:5000/
```

Page `/` : état en direct avec code couleur, nombre de messages, mode réseau + IP (détection best-effort via `network_info.py`, en attendant `wifi_or_ap.sh` au Sprint 8), derniers logs (auto-rafraîchi : statut ~4 s, logs ~8 s, scroll préservé), et un bouton « Sonner maintenant ». API : `/api/status` (état + réseau + nb messages), `/api/logs` (150 dernières lignes), `/api/messages/count`, `/api/ring` (POST, crée `ring_trigger`). Toutes les lectures tolèrent l'absence de fichier (`status.json`, logs) sans jamais renvoyer d'erreur 500. Le port est fixe (`WEB_PORT=5000`) : s'il est déjà occupé, le service s'arrête avec un message explicite plutôt que de basculer sur un autre port ; le port réellement utilisé est écrit dans `active_port.txt`.

### Page Paramètres : choix des fichiers audio

Section **« 🎵 Fichiers audio »** : une liste déroulante par rôle (sonnerie, message générique, message 0 à 9, annonce « aucun message »), alimentée par le contenu de `audio_src/`. Enregistrer écrit le choix dans `audio_config.json` **et convertit aussitôt** les seuls rôles modifiés ; un badge indique par rôle s'il est converti, absent, ou si sa source a changé depuis la dernière conversion (typiquement après une synchro Drive). Le bouton « Tout reconvertir » régénère l'ensemble.

API (administrateur uniquement pour les écritures) : `GET /api/audio/sources`, `POST /api/audio/roles`, `POST /api/audio/reconvert`, `POST /api/audio/test` (joue un fichier généré sur sa sortie, pour vérifier le câblage).

Deux garde-fous : les conversions sont refusées (409) tant que `status.json` n'est pas en `attente` — décoder une dizaine de fichiers sature le Pi Zero 2 W et provoquerait des ratés si un invité était en train de laisser son message — et un verrou non bloquant répond « occupé » plutôt que d'empiler deux décodages. Les noms de fichier venus du formulaire sont validés : ni chemin absolu, ni `..`, ni sous-chemin, et le fichier doit exister dans `audio_src/`.

Le formulaire des fichiers audio est **distinct** de celui des paramètres de fonctionnement : changer un timeout ne déclenche pas une conversion.

## Authentification du dashboard (Sprint 6)

Toutes les routes (HTML et `/api/*`) exigent désormais une session authentifiée, sauf `/login` et les fichiers statiques.

```bash
python3 src/set_password.py   # définit/change le mot de passe du dashboard (saisie masquée)
```

Au premier démarrage sans mot de passe configuré, `dashboard_app.py` utilise le mot de passe par défaut **`livredor`** (documenté, à changer immédiatement via `set_password.py`) — un avertissement est loggé à chaque démarrage tant qu'il n'a pas été changé. Le mot de passe est stocké haché (PBKDF2 via `werkzeug.security`) dans `dashboard_config.json`, jamais en clair. La clé de session Flask (`secret_key.txt`, permissions 600) est générée une fois puis persistée, pour que les sessions ouvertes survivent aux redémarrages du service.

`/login` (formulaire à un seul champ + case « se souvenir de moi » facultative) redirige vers la page initialement demandée après succès (paramètre `next`, protégé contre l'open-redirect) ; `/logout` invalide la session. Anti-brute-force léger : après 3 échecs consécutifs, chaque nouvelle tentative est temporisée (délai progressif, plafonné), journalisée. Session valable `SESSION_LIFETIME_HOURS` (défaut 12 h, couvre la soirée).

⚠️ Le serveur reste en HTTP local (pas de TLS) — ce mot de passe protège contre la curiosité des invités du même réseau, pas contre un attaquant motivé ; c'est le niveau de sécurité voulu (§6).

**Mot de passe admin (parallèle)** : un second mot de passe, indépendant du mot de passe standard, donne aussi accès à la session.

```bash
python3 src/set_admin_password.py   # définit/change le mot de passe admin (saisie masquée)
```

Contrairement au mot de passe standard, il n'a **aucune valeur par défaut** : tant que ce script n'a pas été exécuté, seul le mot de passe standard fonctionne. Les deux mots de passe sont indépendants (changer l'un n'affecte pas l'autre) et stockés séparément, chacun haché, dans `dashboard_config.json`. Pour l'instant, aucune fonctionnalité admin n'est conditionnée dessus — `session["is_admin"]` reflète simplement lequel des deux a été utilisé, en vue d'un usage futur.

## QR codes & provisioning WiFi (Sprint 7)

- **`/qr`** : QR code de l'URL stable du dashboard (`http://livredor.local:5000/`, jamais une IP brute tant que `USE_MDNS=True`), plus un QR WiFi de secours du point d'accès — affiché uniquement quand le Pi est actuellement en mode AP.
- **`/qr/label`** : étiquette imprimable du QR du dashboard, dimensionnée en millimètres (`?taille=NN`, défaut `QR_LABEL_SIZE_MM`, bornée à [10, 200] mm) — page volontairement indépendante du thème du dashboard (fond blanc fixe, pensée pour l'impression).
- **`/wifi`** : photographie un QR WiFi du lieu de réception pour connecter le Pi au réseau, décodé **100 % côté client** avec `jsQR` vendorisé (`static/jsQR.min.js`, via npm + minifié avec `terser`, aucune requête réseau) ; confirmation du SSID détecté avant envoi, avec repli sur une saisie manuelle si la caméra est indisponible. Avertissement affiché si le Pi est en mode AP (une seule antenne : la confirmation coupe la connexion en cours).
- **`/api/wifi/add`** (POST) : crée le profil et connecte via `nmcli device wifi connect` ; erreurs `nmcli` (échec, timeout, absence de l'utilitaire) renvoyées proprement en JSON, jamais de crash.

Toutes ces routes sont protégées par l'authentification du Sprint 6 (aucune n'a été ajoutée à la liste blanche).

## Bascule WiFi/AP (Sprint 8)

`scripts/wifi_or_ap.sh` (exécuté toutes les ~30s par `systemd/wifi-or-ap.timer`) :

- **Déjà connecté à un vrai wifi** (≠ profil AP) : contrôle de qualité (signal courant + ping de la passerelle). Sain → ne rien faire. En échec, un compteur persiste (`WIFI_HEALTH_FILE`, `/run/livre_dor/wifi_health` par défaut) ; après `WIFI_FAIL_THRESHOLD` échecs consécutifs (défaut 3), le SSID est **blacklisté temporairement** (`WIFI_BLACKLIST_MIN`, défaut 10 min, `WIFI_BLACKLIST_FILE`), la connexion coupée, et la recherche d'un candidat reprend **aussitôt** (sans attendre le prochain cycle) — c'est ce qui évite le ping-pong wifi ↔ AP.
- **Sinon** : parmi les profils connus (nommés d'après leur SSID, tels que créés par `nmcli device wifi connect` / `/api/wifi/add`) dont le réseau est visible, non blacklisté et au-dessus de `WIFI_SIGNAL_MIN` (défaut 25 %), tentés par signal décroissant (timeout `CONNECT_TIMEOUT_SEC` chacun). Le point d'accès n'est coupé qu'au moment où une tentative réelle commence — jamais avant, pour ne pas laisser le Pi injoignable entre les deux.
- **Si aucun ne fonctionne** : repli sur le point d'accès `AP_CONNECTION_NAME` (créé s'il n'existe pas encore), SSID `AP_SSID`, IP fixe `AP_IP`.
- Journalisation de chaque décision dans `logs/reseau.log` (rotation manuelle 5×1 Mo, ce log n'étant pas géré par le `RotatingFileHandler` Python).

```bash
# Test manuel (nécessite nmcli/NetworkManager sur le Pi)
./scripts/wifi_or_ap.sh
```

Comme pour le GPIO (Sprint 2), la logique de décision a été vérifiée sans matériel réseau réel en remplaçant `nmcli`/`ping`/`ip` par des scripts factices pilotés par un petit état simulé — voir le detail dans l'historique git.

⚠️ Ce script touche à la connectivité réseau du Pi lui-même : à tester en priorité avec un accès physique/console de secours (checklist §7.6, points 7 et 7bis).

Les unités systemd (`systemd/wifi-or-ap.service` + `.timer`) sont fournies mais pas encore installées automatiquement — l'activation de l'ensemble des services sera finalisée au Sprint 10.

## Synchronisation Google Drive

`src/rclone_sync.py` exécute **deux jambes par cycle**, aux règles volontairement différentes :

| Dossier | Sens | Commande | Pourquoi |
|---|---|---|---|
| `messages/` | montant seul | `rclone copy` (**jamais `sync`**) | Les enregistrements des invités sont le livrable du mariage : aucune action côté Drive ne doit pouvoir les effacer. |
| `audio_src/` | **bidirectionnel** | `rclone bisync` | Pour déposer une sonnerie ou un message des mariés depuis un smartphone et le retrouver sur le Pi — et inversement. |

Les deux dossiers Drive doivent être **distincts et non imbriqués** ; le dashboard refuse toute autre configuration, sinon les enregistrements deviendraient de fait bidirectionnels.

`rclone_config.json` (contrat §8) : `remote`, `dossier`, `intervalle_min`, `actif`, `sources_dossier`, `sources_actif`, `sources_resync_fait`. Les nouvelles clés apparaissent seules sur une installation existante (fusion sur les valeurs par défaut), avec `sources_actif` à `false` : rien ne change tant que la synchro bidirectionnelle n'est pas activée depuis `/rclone`.

Un échec (pas d'internet, remote indisponible...) est toléré : journalisé dans `logs/rclone.log`, sans jamais planter — le prochain cycle du timer retentera.

**Bootstrap `--resync`.** `bisync` a besoin d'un premier passage qui établit l'état de référence. Il est lancé automatiquement au premier cycle, *à condition* que rclone soit assez récent (≥ 1.66) pour `--resync-mode newer` : sans cette option, le resync prendrait le Pi comme référence et supprimerait du Drive tout ce qui n'y est pas encore descendu. Sur un rclone plus ancien, l'initialisation refuse de se faire toute seule et demande le bouton dédié de `/rclone`. Si rclone perd ensuite son état, un `--resync` est retenté une fois, automatiquement. Un verrou `flock` garantit qu'un cycle du timer et le bouton « Synchroniser maintenant » ne se marchent jamais dessus.

```bash
rclone config                       # configuration initiale du remote (une fois, avant l'événement)
python3 src/rclone_sync.py --run      # un cycle complet (messages + sources audio)
python3 src/rclone_sync.py --resync   # (ré)initialise la synchro bidirectionnelle de audio_src/
sudo ./scripts/setup_rclone_systemd.sh   # installation unique : symlinks systemd + règle sudoers ciblée
```

Page **`/rclone`** (protégée) : statut de chaque jambe (dernière synchronisation réussie, fichiers en attente — calculés via `rclone copy --dry-run`, sans rien modifier —, erreurs récentes lues dans `logs/rclone.log`, état d'initialisation du bisync), bouton « Synchroniser maintenant » (`/api/rclone/sync-now`, exécuté en tâche de fond grâce à `threaded=True` sur le serveur pour ne pas geler le reste du dashboard), bouton « Réinitialiser la synchronisation bidirectionnelle » (`/api/rclone/resync`, administrateur, avec confirmation — ce passage peut être long, il se fait à l'installation et non pendant l'événement), et formulaire de configuration. **Changer l'intervalle régénère** `systemd/rclone-sync.timer` puis recharge le service (`systemctl daemon-reload && restart`) automatiquement, sans intervention shell.

`scripts/setup_rclone_systemd.sh` symlinke les unités depuis le dépôt (le dashboard peut donc réécrire `systemd/rclone-sync.timer` directement, sans privilège particulier) et installe une **règle sudoers strictement ciblée** — NOPASSWD limité à `systemctl daemon-reload`, `restart rclone-sync.timer` et `start rclone-sync.timer`, jamais un accès plus large (point de sécurité identifié par la spec, §5.4).

## Services systemd & watchdog (Sprint 10)

```bash
sudo ./scripts/setup_systemd.sh          # symlinke + active livre-dor, dashboard, watchdog, wifi-or-ap
sudo ./scripts/setup_rclone_systemd.sh   # (Sprint 9) synchronisation Google Drive, séparé car règle sudoers dédiée
```

- **`livre-dor.service`** et **`dashboard.service`** : `Restart=always`, `RestartSec=5`, avec `StartLimitIntervalSec=120`/`StartLimitBurst=6` pour absorber une rafale de crashs sans saturer le CPU. `livre-dor.service` n'a **aucune dépendance réseau** (`After=sound.target` uniquement) : il doit continuer à enregistrer même sans aucun réseau (§7.1).
- **`src/watchdog.py`** (exécuté toutes les 2 min par `livre-dor-watchdog.timer`) : jamais d'abandon définitif —
  1. si un service (`livre-dor`, `dashboard`, `wifi-or-ap`, `rclone-sync`) est en état `failed` (`StartLimitBurst` épuisé), `systemctl reset-failed` puis `restart` sont retentés à chaque passage (intervalle plus long que `RestartSec`, pour laisser une panne transitoire se résorber) ;
  2. si `status.json` n'a pas été mis à jour depuis `WATCHDOG_STALE_AFTER_SEC` (défaut 300 s), `livre-dor.service` est redémarré même s'il n'est pas techniquement `failed` (processus gelé plutôt que planté).
- `wifi-or-ap.service` et `rclone-sync.service` (Sprints 8-9) reçoivent les mêmes garde-fous `StartLimit*`, désormais couverts par le même watchdog.
- Logs consultables via `journalctl -u <unité> -f` en complément des fichiers dans `logs/`.

Vérifié (sans systemd réel dans ce sandbox) : syntaxe de toutes les unités validée avec `systemd-analyze verify`, et logique du watchdog testée avec `systemctl` simulé (service en échec, `status.json` frais/périmé/absent, plusieurs services en échec simultanément, absence de double redémarrage redondant).

## Transcription batch (Sprint 11, optionnelle, hors événement)

```bash
./scripts/install_whisper.sh                 # une fois : compile whisper.cpp + télécharge le modèle tiny q5_0
python3 src/transcribe_batch.py --dry-run    # liste les messages pas encore transcrits
python3 src/transcribe_batch.py              # transcrit (refuse si livre-dor.service est actif)
```

⚠️ **Ne jamais lancer pendant l'événement** : whisper.cpp est trop lent sur un Pi Zero 2 W pour tourner en même temps que l'enregistrement en direct (§5.5). `run_batch()` **refuse de démarrer si `livre-dor.service` est actif** (`--force` pour outrepasser, déconseillé) — à réserver à un usage nocturne sur le Pi, ou après transfert des WAV vers une machine plus puissante.

`src/transcribe_batch.py` ne traite que les `messages/*.wav` sans `.txt` associé (incrémental, idempotent) : reformatage en 16 kHz mono via `ffmpeg` (déjà une dépendance du projet) dans un fichier temporaire, transcription par `whisper-cli`, écriture du `.txt` à côté du WAV — **le fichier audio source n'est jamais lu en écriture, modifié ni supprimé**. Un échec sur un fichier (modèle corrompu, audio illisible...) est journalisé et n'interrompt jamais le reste du lot.

Vérifié par un harnais dédié (15 contrôles) : détection incrémentale, refus si le service est actif (et `--force` qui l'outrepasse sans même consulter `systemctl`), échecs propres si le binaire ou le modèle whisper sont absents, transcription réussie avec non-modification vérifiée du WAV (contenu **et** date de modification identiques avant/après), idempotence au second passage, et isolation d'un échec ponctuel sans effet sur les autres fichiers du lot.

## Recette finale (Sprint 12)

- [`docs/guide_installation.md`](docs/guide_installation.md) : câblage, dépendances, tableau complet des paramètres de configuration, réglage du potentiomètre PAM8403, procédure de premier démarrage.
- [`docs/checklist_mise_en_service.md`](docs/checklist_mise_en_service.md) : checklist dérivée du §7.6, à dérouler et signer sur le matériel final avant l'événement.
- **`src/load_test.py`** : simule plusieurs cycles décroché/composition/enregistrement/raccroché à la suite pour détecter toute fuite de sous-processus (`aplay`/`arecord` orphelins) ou de fichiers — utilise un dossier d'enregistrement temporaire séparé, jamais `messages/`.

  ```bash
  python3 src/load_test.py --cycles 20
  ```

La plupart des points de la checklist (câblage réel, sens logique des contacts, redémarrage à froid du Pi, bascule WiFi/AP physique...) exigent le matériel assemblé et ne peuvent pas être vérifiés dans cet environnement de développement. Ce qui a pu être vérifié ici : la logique du test de charge elle-même (comptage de fichiers, détection d'orphelins, avec audio simulé — 5 contrôles), et la résilience des écritures atomiques de `status.json` (Sprint 4) face à une coupure brutale : 60 simulations de `SIGKILL` en pleine écriture, jamais de fichier corrompu.

## Mode restitution (Sprint 13, après l'événement)

Le mariage passé, le téléphone devient un **lecteur des messages laissés par les invités** : décrocher, composer au cadran le numéro d'un message, l'écouter. Voir le [§5.7 de la spécification](docs/specification_livre_dor_telephonique.md).

- Numéro de **4 chiffres au maximum** (`RESTITUTION_DIGITS_MAX`). Composer `1` puis attendre 3 s (`RESTITUTION_INTERDIGIT_SEC`) lit le premier message ; composer `3695` ferme la saisie au 4ᵉ chiffre et lance la lecture aussitôt, sans qu'un 5ᵉ chiffre soit possible.
- Messages numérotés **1..N dans l'ordre chronologique** d'enregistrement (horodatage du nom de fichier, repli sur la date de modification pour un nom non conforme).
- Numéro au-delà du nombre de messages → **le dernier** est lu. Numéro nul (dix impulsions) → le premier.
- **Aucune parole, aucun enregistrement** : ni message des mariés, ni bip, ni micro. Le mode est en lecture seule sur `messages/`, et l'état `enregistrement` refuse explicitement de démarrer quand il est actif.
- **Sonnerie neutralisée**, branche « appel entrant » comprise ; un `ring_trigger` déposé par le dashboard est consommé sans sonner.
- Après un message, **silence jusqu'au raccroché** : raccrocher puis redécrocher pour en écouter un autre.

**Bascule** depuis le dashboard, page **Mode** (`/mode`) : elle écrit `mode_config.json`, relu à chaud par la machine à états à chaque tour de la boucle d'attente. Prise en compte en moins d'une seconde, sans redémarrage du service, et jamais au milieu d'une communication. `MODE_RESTITUTION` ne fixe que la valeur du fichier à sa création.

**Annonce optionnelle** : déposer `audio_src/aucun_message.*` puis relancer `python3 src/prepare_audio.py` pour annoncer qu'aucun message n'est disponible ; sans elle, le bip sert de repli.

- **`src/restitution_test.py`** : valide les règles du mode sans Raspberry Pi ni carte son — `PhoneInputs` piloté à la main (séquence réelle du cadran), `audio_io.play`/`record` remplacés par des doublures, dossier de messages temporaire (jamais `messages/`).

  ```bash
  python3 src/restitution_test.py
  ```

  11 scénarios, 20 contrôles : « 1 » + attente, 4 chiffres avec 5ᵉ ignoré, numéro dans les bornes, numéro nul, `messages/` vide, raccroché pendant la saisie puis pendant la lecture, chiffre composé pendant la lecture, absence de sonnerie et d'enregistrement sur une attente prolongée, refus d'enregistrer après bascule en pleine communication, et tri chronologique (collisions `_k`, nom non conforme, exclusion des `.txt` de transcription et des sous-dossiers).

**Empreinte sur le Raspberry Pi** (mesurée) : le mode est en **lecture seule** sur `messages/` — parcourir les messages n'écrit rien. En attente il n'écrit **aucun octet** sur la carte SD, et `mode_config.json` est servi par le page cache (`read_bytes = 0`). RSS stable à 15,7 Mo sur 150 appels enchaînés, sans fuite de descripteur ni de thread. Un appel complet coûte 28 Ko (7 écritures de `status.json`). À comparer aux ~10 Mo écrits par message de 2 min en mode mariage.

**Point de vigilance matériel** : le micro est câblé sur l'entrée **Aux gauche**, dupliquée sur les deux canaux DAI par `scripts/audio-setup.sh` (numids 89 et 90) — les deux pistes d'un enregistrement portent donc le même signal. À vérifier sur la première prise réelle : si la piste droite ressortait muette, le second écouteur n'entendrait rien en mode restitution. La lecture, elle, est toujours commutée sur la sortie casque, jamais sur le haut-parleur de sonnerie. `RESTITUTION_SOUND_CARD` reste disponible comme échappatoire de routage ALSA, sans modification de code.

## Tests unitaires par fonction (`tests/`)

Un script par fonction du téléphone, **indépendant des autres**, à lancer seul dans un terminal — voir [`tests/README.md`](tests/README.md) pour le détail.

```bash
python3 tests/test_decroche.py          # une fonction (code de sortie 0 ou 1)
python3 tests/run_tous.py               # les huit, avec un bilan final
```

| Script | Fonction validée |
|---|---|
| `test_decroche.py` | Décroché : tonalité, purge du cadran, appel entrant, restitution |
| `test_raccroche.py` | Raccroché à chaque étape du parcours, anti-rebond du crochet |
| `test_composition.py` | Cadran rotatif : impulsions → chiffre, numéro multi-chiffres |
| `test_lecture.py` | Lecture d'un message : sélection du fichier, `aplay`, interruption |
| `test_enregistrement.py` | Enregistrement : nom horodaté, `arecord`, espace disque |
| `test_sonnerie.py` | Sonnerie périodique, `ring_trigger`, fenêtre de grâce |
| `test_mode.py` | Bascule mariage / restitution à chaud (`mode_config.json`) |
| `test_status.py` | `status.json`, battement de cœur et décisions du watchdog |

Aucune dépendance à installer (ni `pytest`, ni `flask`, ni `pydub`, ni `RPi.GPIO`) : les scripts appellent directement les fonctions de `src/` et lisent les paramètres déjà configurés (variable d'environnement, puis `custom_config.json` du dashboard, puis défaut de `config.py`).

**En simulation** (par défaut), `PhoneInputs` est piloté à la main, `aplay`/`arecord` sont remplacés par des doublures et toute l'arborescence de données est redirigée vers un dossier temporaire : `messages/`, `audio/` et `status.json` ne sont jamais touchés.

**Sur le matériel** (`--reel`), les mêmes fonctions du service sont appelées avec les broches, la carte son et les durées de l'installation — le script affiche les paramètres en vigueur et leur origine, puis demande de décrocher, composer, écouter, et vérifie ce que le matériel répond. C'est la recette physique du §7.6, automatisée :

```bash
python3 tests/test_composition.py --reel     # composez les chiffres demandés
python3 tests/run_tous.py --reel             # recette physique complète
```

## Statut

Développement terminé — les 12 sprints du plan de développement sont réalisés, plus le Sprint 13 (mode restitution, à activer après l'événement). La checklist de mise en service reste à dérouler sur le matériel physique assemblé avant l'événement, et sa section 9bis après (voir [`docs/checklist_mise_en_service.md`](docs/checklist_mise_en_service.md)).
