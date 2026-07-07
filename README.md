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

## Statut

Projet en cours de développement — voir le plan de développement pour l'avancement par sprint. Sprints 0 (environnement) et 1 (pipeline audio) réalisés.
