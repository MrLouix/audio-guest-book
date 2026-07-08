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

⚠️ Lecture seule et **sans authentification** à ce stade (Sprint 6) : ne pas exposer ce dashboard sur un réseau non maîtrisé avant l'implémentation du mot de passe.

Page `/` : état en direct avec code couleur, nombre de messages, mode réseau + IP (détection best-effort via `network_info.py`, en attendant `wifi_or_ap.sh` au Sprint 8), derniers logs (auto-rafraîchi : statut ~4 s, logs ~8 s, scroll préservé), et un bouton « Sonner maintenant ». API : `/api/status` (état + réseau + nb messages), `/api/logs` (150 dernières lignes), `/api/messages/count`, `/api/ring` (POST, crée `ring_trigger`). Toutes les lectures tolèrent l'absence de fichier (`status.json`, logs) sans jamais renvoyer d'erreur 500. Le port est fixe (`WEB_PORT=5000`) : s'il est déjà occupé, le service s'arrête avec un message explicite plutôt que de basculer sur un autre port ; le port réellement utilisé est écrit dans `active_port.txt`.

## Statut

Projet en cours de développement — voir le plan de développement pour l'avancement par sprint. Sprints 0 (environnement), 1 (pipeline audio), 2 (machine à états, scénario nominal), 3 (sonnerie, appel entrant), 4 (fiabilité) et 5 (dashboard web, lecture seule) réalisés.
