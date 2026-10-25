# Livre d'or téléphonique (Socotel S63)

Un téléphone à cadran Socotel S63 transformé en livre d'or audio pour un mariage : les invités décrochent, composent un chiffre, écoutent un message des mariés, puis laissent le leur après le bip. Après la fête, le même téléphone sert à réécouter les messages, et le dashboard à les trier.

## Comment ça marche

1. **Le téléphone sonne** de temps en temps pour attirer les invités (réglable, coupé par défaut).
2. **L'invité décroche** : tonalité, comme sur un vrai poste. S'il décroche pendant la sonnerie, c'est un « appel entrant » : un message des mariés lui est joué directement.
3. **Il compose un chiffre** au cadran : le message des mariés associé à ce chiffre est joué.
4. **Bip, puis il parle.** Le message est enregistré, nettoyé (ronflement secteur, souffle, niveau), puis envoyé sur Google Drive.
5. **Après l'événement**, le téléphone passe en mode restitution : on compose le numéro d'un message pour l'écouter. Le dashboard permet de trier les messages, depuis le Pi ou depuis un PC.

Tout tourne sur un **Raspberry Pi Zero 2 W** avec une carte son **IQaudio Codec Zero** : le cadran et le crochet sur des GPIO, l'écouteur et le micro du combiné sur le codec, un haut-parleur de sonnerie sur sa sortie ligne. Un **dashboard web** (`http://livredor.local:5000/`) permet de suivre et régler l'ensemble depuis un téléphone.

## Démarrage rapide

Sur le Raspberry Pi (Raspberry Pi OS Lite, Bookworm), une fois le matériel câblé :

```bash
./scripts/install.sh                  # paquets, hostname livredor, venv Python, carte son
python3 src/set_password.py           # mot de passe du dashboard (défaut : livredor)
sudo ./scripts/setup_systemd.sh       # démarrage automatique et supervision
```

Puis, dans le dashboard : choisir les fichiers audio (page Paramètres) et configurer Google Drive (page Google Drive). Le pas-à-pas complet (câblage, Drive, premier démarrage) est dans le **[guide d'installation](docs/guide_installation.md)**, et la recette à dérouler avant le jour J dans la **[checklist de mise en service](docs/checklist_mise_en_service.md)**.

## Documentation

| Document | Pour… |
|---|---|
| [Guide d'installation](docs/guide_installation.md) | assembler, installer et configurer l'appareil ; tous les paramètres |
| [Checklist de mise en service](docs/checklist_mise_en_service.md) | valider le matériel final avant l'événement |
| [Le téléphone](docs/telephone.md) | parcours des invités, tonalité, sonnerie, fiabilité, mise au point du cadran |
| [Audio](docs/audio.md) | sorties du codec, fichiers des mariés, traitement des messages |
| [Dashboard](docs/dashboard.md) | pages, paramètres, mots de passe, QR codes et WiFi |
| [Réseau, synchro et services](docs/reseau_synchro.md) | bascule WiFi / point d'accès, Google Drive, systemd et watchdog |
| [Après l'événement](docs/apres_evenement.md) | mode restitution, tri des messages (Pi et PC), transcription |
| [Tests](tests/README.md) | tests par fonction, en simulation ou sur le matériel, et outils de diagnostic |
| [Spécification](docs/specification_livre_dor_telephonique.md) | cahier des charges complet (les `§` cités dans la doc y renvoient) |
| [Plan de développement](docs/dev_plan.md) | historique en sprints, critères d'acceptation |
| [Banc audio](docs/banc_audio/README.md) | mesures du micro électret et choix de la capture |

## Tests

Un script par fonction du téléphone, sans dépendance à installer, en simulation par défaut :

```bash
python3 tests/run_tous.py          # tous les tests, avec un bilan
python3 tests/run_tous.py --reel   # recette physique sur le matériel assemblé
```

Détails, liste des scripts et outils de diagnostic du cadran : [tests/README.md](tests/README.md).

## Organisation du dépôt

```
src/          code Python (machine à états, dashboard, audio, synchro…)
templates/    pages HTML du dashboard        static/   CSS, JS
scripts/      installation, réglages audio, bascule réseau
systemd/      services et timers
docs/         documentation
tests/        tests par fonction
audio_src/    sources audio déposées (synchronisé avec le Drive)
audio/        fichiers convertis, générés        messages/   enregistrements des invités
```

## Statut

Développement terminé : les sprints 0 à 14 du [plan de développement](docs/dev_plan.md) sont réalisés. Reste à dérouler la [checklist de mise en service](docs/checklist_mise_en_service.md) sur le matériel assemblé avant l'événement, et sa section 9bis après.

## Contribuer

Les contributions sont bienvenues : voir [`CONTRIBUTING.md`](CONTRIBUTING.md) et le [code de conduite](CODE_OF_CONDUCT.md). Une faille de sécurité se signale en privé (voir [`SECURITY.md`](SECURITY.md)).

## Licence

[MIT](LICENSE) © 2026 MrLouix. `static/jsQR.min.js` est distribué sous licence Apache 2.0 (voir `static/jsQR.LICENSE.txt`).
