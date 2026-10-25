# Audio : sorties, fichiers et traitement des messages

[← README](../README.md) · Spécification : §4 (audio), §4.3 (enregistrement)

## Les deux sorties du codec (IQaudio Codec Zero)

| Sortie du codec | Matériel | Ce qui y passe |
|---|---|---|
| **line out** | 1 haut-parleur **mono** de sonnerie, qui lit la piste **gauche** | `ring_out.wav` |
| **headphone** (stéréo) | écouteur du combiné (**L**) + écouteur secondaire (**R**) | tonalité, messages des mariés, bip, mode restitution |

Le codec ne peut pas alimenter les deux en même temps : il est **commuté avant chaque lecture**, line out pour la sonnerie, casque pour tout le reste. Tous les fichiers joués sont donc **stéréo avec les deux pistes identiques**, sans panning.

Tous les réglages du DA7213 (numids `amixer`) vivent dans `scripts/audio-setup.sh`, et nulle part ailleurs : un numid n'est qu'un index dans l'énumération des contrôles ALSA, qui peut glisser d'une version de driver à l'autre. `src/alsa_io.py` ne connaît que trois mots : `lineout`, `headphone`, `both`.

```bash
./scripts/audio-setup.sh status           # état courant (entrée, routage, sorties)
./scripts/audio-setup.sh headphone        # configuration complète, sortie casque
./scripts/audio-setup.sh switch-lineout   # bascule de sortie seule (quelques ms)
python3 src/alsa_io.py status             # même chose, vu depuis Python
```

`livre_dor.py` applique la configuration complète une fois au démarrage, puis ne fait plus que des bascules rapides. Un échec de commutation est journalisé mais n'empêche jamais la lecture : le mauvais haut-parleur vaut mieux que le silence en plein mariage.

Volumes, gain du micro et niveaux maximum se règlent depuis le dashboard : voir [Dashboard → Paramètres](dashboard.md#paramètres-de-fonctionnement).

## Fichiers audio : `audio_src/` et `audio/`

- **`audio_src/`** : les fichiers sources bruts, tels que déposés (mp3, m4a, wav…), **avec leur nom d'origine**. Synchronisé avec Google Drive dans les deux sens : on peut y déposer une sonnerie ou un message depuis un smartphone.
- **`audio/`** : les WAV convertis, prêts à être joués. Entièrement généré, jamais synchronisé, jamais édité à la main.

La conversion produit du **48 kHz, 16 bits (S16_LE), stéréo L = R**. La capture, elle, se fait en **16 kHz** stéréo L = R : c'est là que le plancher de bruit du micro électret est le plus bas (−5,4 dB mesurés au [banc](banc_audio/README.md)), et le parcours ne lit ni n'enregistre jamais en même temps.

### Choisir le fichier de chaque rôle

Le fichier qui joue chaque rôle (sonnerie, message générique, message 0 à 9, annonce « aucun message ») se choisit **dans le dashboard**, page Paramètres, par liste déroulante sur le contenu de `audio_src/` : aucun renommage nécessaire. Le choix est enregistré dans `audio_config.json` ; un rôle sans choix explicite retombe sur l'ancienne convention de nommage (`audio_src/sonnerie.*`), ce qui laisse fonctionner une installation antérieure telle quelle.

En ligne de commande :

```bash
python3 src/prepare_audio.py                 # (re)génère tout dans audio/
python3 src/prepare_audio.py --role sonnerie  # un seul rôle
python3 src/prepare_audio.py --play-all       # rejoue chaque fichier sur SA sortie
```

- `tonalite.wav` (440 Hz continu) et `bip.wav` (800 Hz, 600 ms, encadré de silence) sont **générés par synthèse**, sans fichier source.
- Les messages (générique, 0 à 9, « aucun message ») sont **normalisés** : la voix (95e centile des blocs de 20 ms) est ramenée à −16 dBFS, soit environ −17 LUFS, la sonie du bip et de la tonalité. Gain plafonné à +30 dB et borné par la crête (−1 dBFS) pour ne jamais écrêter. Sans cela, un message enregistré au smartphone sortait 17 dB sous le bip. La sonnerie n'est pas normalisée.
- Un rôle sans source est simplement ignoré : `livre_dor.py` retombe sur `message_generique.wav`.
- Chaque fichier est écrit via un temporaire puis `os.replace()` : une reconversion lancée depuis le dashboard ne peut pas faire lire un WAV tronqué à `aplay`.

## Traitement des messages des invités

À chaque raccroché, `src/traitement_audio.py` traite le message en arrière-plan, en priorité basse : le téléphone est aussitôt prêt pour l'invité suivant. La chaîne est volontairement légère :

1. **Anti-ronflement** : le 50 Hz secteur et ses harmoniques jusqu'à 4 kHz sont suivis en fréquence et soustraits, ~1 Hz de spectre retiré par raie ; la voix n'est pas touchée.
2. **Débruitage léger** : atténuation spectrale plafonnée à `TRAITEMENT_DEBRUITAGE_DB` (5 dB par défaut), sur un bruit estimé dans les pauses ; sauté si le message n'en a pas.
3. **Normalisation** du niveau de la voix à −20 dBFS (gain plafonné à +30 dB, limiteur contre les claquements).

L'ancienne chaîne du banc (declip, despike, coupe-bandes, `noisered`, expandeur) rendait une voix faussée et métallique.

Le brut est conservé dans `messages/brut/` (et synchronisé sur le Drive avec le reste) ; `messages/<nom>.wav` est remplacé atomiquement par la version traitée. Un échec laisse le brut en place.

Réglages : `TRAITEMENT_ACTIF`, `TRAITEMENT_ANTI_RONFLEMENT`, `TRAITEMENT_DEBRUITAGE_DB`, `TRAITEMENT_PROFIL_DEBUT_SEC`, `TRAITEMENT_NORMALISATION`, `TRAITEMENT_NIVEAU_VOIX_DBFS`, `TRAITEMENT_GAIN_MAX_DB`.

```bash
python3 src/traitement_audio.py messages/<nom>.wav   # retraiter un message (repart du brut)
python3 src/traitement_audio.py --en-attente         # tous les messages pas encore traités
```

## Primitives de lecture et d'enregistrement

`src/audio_io.py` se teste isolément :

```bash
python3 src/audio_io.py play audio/tonalite.wav
python3 src/audio_io.py record test.wav --duration 5
```
