# Livre d'or téléphonique (Socotel S63)

Transformation d'un téléphone à cadran vintage Socotel S63 en livre d'or automatique pour un mariage : les invités décrochent, composent un chiffre, écoutent un message des mariés, puis laissent le leur après un bip.

## Documentation

- [`docs/specification_livre_dor_telephonique.md`](docs/specification_livre_dor_telephonique.md) — cahier des charges technique complet (matériel, câblage, architecture logicielle, fiabilité).
- [`docs/dev_plan.md`](docs/dev_plan.md) — plan de développement détaillé en sprints.

## Installation (Sprint 0)

Sur le Raspberry Pi (Raspberry Pi OS Lite / Bookworm) :

```bash
./scripts/install.sh
```

Installe les paquets système (`alsa-utils`, `rclone`, `ffmpeg`, `avahi-daemon`), configure le hostname `livredor` (mDNS), crée un environnement virtuel Python avec les dépendances de `requirements.txt`, et vérifie l'arborescence du projet ainsi que la présence de la carte son USB.

Les paramètres (broches GPIO, carte son, seuils réseau, etc.) sont centralisés dans `src/config.py` et surchargeables par variables d'environnement — voir §9 de la spécification.

Le code Python vit dans `src/` ; l'arborescence de données (`audio_src/`, `audio/`, `messages/`, `logs/`, `static/`, `templates/`, `status.json`...) reste à la racine du projet, conformément à l'arborescence de déploiement du §8. Tous les scripts s'exécutent directement (`python3 src/<script>.py`), sans installation du projet ni `PYTHONPATH` à configurer.

## Pré-traitement audio (Sprint 1)

Placer les fichiers sources bruts dans `audio_src/` (`sonnerie.*`, `message_generique.*`, `message_0.*` … `message_9.*`), puis :

```bash
python3 src/prepare_audio.py            # génère tous les fichiers panés/gainés dans audio/
python3 src/prepare_audio.py --play-all  # rejoue chaque fichier généré, pour vérifier le panning au casque
```

`tonalite.wav` (440+480 Hz) et `bip.wav` sont générés par synthèse, sans fichier source. Un chiffre sans fichier `message_N.*` correspondant est simplement ignoré (le fallback `message_generique.wav` sera utilisé par `livre_dor.py`).

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

En attente, `livre_dor.py` sonne (`audio/ring_out.wav`) toutes les `RING_INTERVAL_SEC` (défaut 90 s), et immédiatement si le dashboard crée le fichier `ring_trigger` (consommé puis supprimé). Un décroché pendant la sonnerie ou dans les `RING_ANSWER_GRACE_SEC` (défaut 5 s) qui suivent sa fin simule un vrai appel entrant : la sonnerie est coupée immédiatement, **aucune tonalité n'est jouée et le cadran est ignoré** (impulsions journalisées en debug, sans effet), un message est tiré au hasard parmi tous les `message_N.wav` + `message_generique.wav` disponibles (jamais deux fois de suite le même), puis bip → enregistrement, comme dans le flux nominal. Un décroché hors de cette fenêtre suit le flux nominal habituel (tonalité + cadran).

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

## Synchronisation Google Drive (Sprint 9)

`src/rclone_sync.py` exécute `rclone copy` (**jamais `sync`** : n'ajoute que les fichiers nouveaux/modifiés, ne supprime jamais rien ni en local ni sur le Drive) vers le remote/dossier configurés dans `rclone_config.json` (contrat §8 : `remote`, `dossier`, `intervalle_min`, `actif`). Un échec (pas d'internet, remote indisponible...) est toléré : journalisé dans `logs/rclone.log`, sans jamais planter — le prochain cycle du timer retentera.

```bash
rclone config                       # configuration initiale du remote (une fois, avant l'événement)
python3 src/rclone_sync.py --run    # exécute un cycle de synchronisation immédiatement
sudo ./scripts/setup_rclone_systemd.sh   # installation unique : symlinks systemd + règle sudoers ciblée
```

Page **`/rclone`** (protégée) : statut (dernière synchronisation réussie, fichiers en attente — calculés via `rclone copy --dry-run`, sans rien modifier —, erreurs récentes lues dans `logs/rclone.log`), bouton « Synchroniser maintenant » (`/api/rclone/sync-now`, exécuté en tâche de fond grâce à `threaded=True` sur le serveur pour ne pas geler le reste du dashboard), et formulaire de configuration. **Changer l'intervalle régénère** `systemd/rclone-sync.timer` puis recharge le service (`systemctl daemon-reload && restart`) automatiquement, sans intervention shell.

`scripts/setup_rclone_systemd.sh` symlinke les unités depuis le dépôt (le dashboard peut donc réécrire `systemd/rclone-sync.timer` directement, sans privilège particulier) et installe une **règle sudoers strictement ciblée** — NOPASSWD limité à `systemctl daemon-reload`, `restart rclone-sync.timer` et `start rclone-sync.timer`, jamais un accès plus large (point de sécurité identifié par la spec, §5.4).

## Statut

Projet en cours de développement — voir le plan de développement pour l'avancement par sprint. Sprints 0 (environnement), 1 (pipeline audio), 2 (machine à états, scénario nominal), 3 (sonnerie, appel entrant), 4 (fiabilité), 5 (dashboard web), 6 (authentification), 7 (QR codes, provisioning WiFi), 8 (bascule WiFi/AP) et 9 (synchronisation Google Drive) réalisés.
