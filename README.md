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

Les paramètres (broches GPIO, carte son, seuils réseau, etc.) sont centralisés dans `config.py` et surchargeables par variables d'environnement — voir §9 de la spécification.

## Pré-traitement audio (Sprint 1)

Placer les fichiers sources bruts dans `audio_src/` (`sonnerie.*`, `message_generique.*`, `message_0.*` … `message_9.*`), puis :

```bash
python3 prepare_audio.py            # génère tous les fichiers panés/gainés dans audio/
python3 prepare_audio.py --play-all  # rejoue chaque fichier généré, pour vérifier le panning au casque
```

`tonalite.wav` (440+480 Hz) et `bip.wav` sont générés par synthèse, sans fichier source. Un chiffre sans fichier `message_N.*` correspondant est simplement ignoré (le fallback `message_generique.wav` sera utilisé par `livre_dor.py`).

Les primitives de lecture/enregistrement bas niveau (`audio_io.py`) peuvent être testées isolément :

```bash
python3 audio_io.py play audio/tonalite.wav
python3 audio_io.py record test.wav --duration 5
```

## Machine à états — scénario nominal (Sprint 2)

```bash
python3 livre_dor.py --test   # affichage temps réel des 3 GPIO, pour valider le câblage
python3 livre_dor.py          # démarre la machine à états (nécessite un vrai Raspberry Pi)
```

Parcours implémenté (§1.2, scénario « appel sortant ») :
attente → décroché (tonalité 440+480 Hz) → dès la première impulsion, la tonalité s'arrête → numérotation (comptage des impulsions, chiffre validé au retour du cadran au repos, 10 impulsions = chiffre 0) → lecture de `audio/message_N.wav` (repli sur `message_generique.wav` si absent) → bip → enregistrement (`messages/message_AAAA-MM-JJ_HH-MM-SS.wav`, jamais d'écrasement) → retour à l'attente. Toute lecture est interrompue immédiatement au raccroché ; l'enregistrement est plafonné à `MAX_RECORD_SEC`.

`gpio_io.py` isole l'accès matériel (RPi.GPIO, callbacks avec anti-rebond `bouncetime`) derrière `PhoneInputs`, un état partagé thread-safe indépendant du matériel — ce qui permet de vérifier toute la logique de la machine à états sans Raspberry Pi (audio et GPIO simulés) avant le déploiement.

## Sonnerie & scénario « appel entrant » (Sprint 3)

En attente, `livre_dor.py` sonne (`audio/ring_out.wav`) toutes les `RING_INTERVAL_SEC` (défaut 90 s), et immédiatement si le dashboard crée le fichier `ring_trigger` (consommé puis supprimé). Un décroché pendant la sonnerie ou dans les `RING_ANSWER_GRACE_SEC` (défaut 5 s) qui suivent sa fin simule un vrai appel entrant : la sonnerie est coupée immédiatement, **aucune tonalité n'est jouée et le cadran est ignoré** (impulsions journalisées en debug, sans effet), un message est tiré au hasard parmi tous les `message_N.wav` + `message_generique.wav` disponibles (jamais deux fois de suite le même), puis bip → enregistrement, comme dans le flux nominal. Un décroché hors de cette fenêtre suit le flux nominal habituel (tonalité + cadran).

## Statut

Projet en cours de développement — voir le plan de développement pour l'avancement par sprint. Sprints 0 (environnement), 1 (pipeline audio), 2 (machine à états, scénario nominal) et 3 (sonnerie, appel entrant) réalisés.
