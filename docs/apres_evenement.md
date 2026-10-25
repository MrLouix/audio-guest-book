# Après l'événement : réécouter, trier, transcrire

[← README](../README.md) · Spécification : §5.5 (transcription), §5.7 (restitution), §5.8 (tri)

## Mode restitution

Le mariage passé, le téléphone devient un **lecteur des messages laissés par les invités** : décrocher, composer au cadran le numéro d'un message, l'écouter.

**Bascule** depuis le dashboard, page **Mode** (`/mode`). Elle est prise en compte en moins d'une seconde, sans redémarrer le service et jamais au milieu d'une communication. `MODE_RESTITUTION` ne fixe que la valeur de départ.

- Numéro de **4 chiffres au maximum** (`RESTITUTION_DIGITS_MAX`). Composer `1` puis attendre 3 s (`RESTITUTION_INTERDIGIT_SEC`) lit le premier message ; composer `3695` lance la lecture au 4ᵉ chiffre, sans attendre.
- Messages numérotés **1..N dans l'ordre chronologique** (horodatage du nom de fichier). Les messages marqués « supprimé » sur la page Messages ne sont pas lus et ne comptent pas.
- Numéro au-delà du dernier message → **le dernier** est lu. Numéro nul (dix impulsions) → le premier.
- **Aucun enregistrement possible** : ni message des mariés, ni bip, ni micro. Le mode ne modifie jamais rien dans `messages/`.
- **Sonnerie neutralisée**, appel entrant compris ; un `ring_trigger` est consommé sans sonner.
- Après un message, **silence jusqu'au raccroché** : raccrocher puis redécrocher pour en écouter un autre.
- **Annonce optionnelle** : déposer `audio_src/aucun_message.*` puis lancer `python3 src/prepare_audio.py` pour annoncer qu'aucun message n'est disponible ; sans elle, le bip sert de repli.

**Empreinte sur le Pi** (mesurée) : en attente, le mode n'écrit aucun octet sur la carte SD ; mémoire stable sur 150 appels enchaînés ; un appel complet coûte 28 Ko (écritures de `status.json`).

**Point de vigilance** : le micro est dupliqué sur les deux pistes de chaque enregistrement par `audio-setup.sh`. À vérifier sur la première prise réelle : si la piste droite était muette, le second écouteur n'entendrait rien en restitution. `RESTITUTION_SOUND_CARD` permet de router la lecture autrement, sans toucher au code.

Vérification de la logique, sans matériel : `python3 src/restitution_test.py` (13 scénarios, dossier temporaire, jamais `messages/`).

## Trier les messages

Page **Messages** du dashboard (`/messages`), ouverte à tout utilisateur connecté : c'est le client qui décide ce qu'il garde.

- Une ligne par message : le nom du fichier (il porte la date et l'heure), et dessous le numéro de restitution, le score, la durée et le statut.
- Tri par date ou par score ; filtres par statut, score minimal et nom ; sélection de plusieurs lignes.
- Actions, sur une ligne ou sur la sélection : **▶ Écouter** (dans le navigateur), **✔ Conserver** (réversible), **🗑 Supprimer**, **↺ Restaurer**.

**Aucun fichier audio n'est touché.** Supprimer et restaurer ne changent qu'un statut, écrit avec le score dans `audio_src/index_messages.json`. Le mode restitution lit tous les messages sauf les supprimés ; sans ce fichier, il lit tout. Le brut et la transcription restent en place.

**Score sur 10**, calculé sur le message traité, en arrière-plan et jamais pendant un appel :

| Critère | Effet |
|---|---|
| durée < 5 s | 0 : pas de texte |
| rapport signal sur bruit (voix − bruit de fond) | 6 dB → 0, 30 dB → 10, linéaire entre les deux |
| moins de 30 % de parole | note réduite d'autant |
| plus de 3 min | note divisée par deux |

Seuils `SCORE_*` dans `src/config.py`. Un score n'est recalculé que si le fichier change.

### Depuis un PC

Le tri peut se faire sur un PC, sans copier le projet, à partir des dossiers synchronisés par **Google Drive pour ordinateur** : `MariageGuestBook` (messages) et `MariageGuestBookSources` (`audio_src/`).

1. Installer **Python 3** récent (python.org, 3.14 ou plus ; cocher « Add python.exe to PATH »), puis une fois :
   ```
   py -m pip install flask numpy
   ```
2. Sur la page Messages du dashboard, encadré « Trier depuis un PC », télécharger **`tri_messages.pyz`** (~40 Ko) et le poser dans le dossier qui contient les deux dossiers synchronisés.
3. Le lancer d'un double-clic, ou `py tri_messages.pyz`. La page s'ouvre sur `http://127.0.0.1:5050/messages` (accessible depuis ce PC seulement, sans mot de passe).

Autres façons de le lancer :

```
py tri_messages.pyz "G:\Mon Drive"                        # dossiers ailleurs
py tri_messages.pyz --messages D:\msg --audio-src D:\src   # chaque dossier désigné
py src\tri_messages.py "G:\Mon Drive"                     # depuis une copie du dépôt
python3 src/paquet_pc.py                                  # construire l'archive soi-même
```

L'index étant dans `audio_src/`, synchronisé dans les deux sens, les choix faits sur le PC redescendent sur le Pi à la synchro suivante, et inversement (la synchro bidirectionnelle doit être activée sur `/rclone`). Si les deux côtés ont modifié l'index entre deux synchros, la copie laissée par le conflit (`index_messages (1).json`…) est fusionnée message par message, la décision la plus récente l'emportant, puis supprimée.

## Transcription (optionnelle)

```bash
./scripts/install_whisper.sh                 # une fois : compile whisper.cpp + modèle tiny q5_0
python3 src/transcribe_batch.py --dry-run    # liste les messages pas encore transcrits
python3 src/transcribe_batch.py              # transcrit (refuse si livre-dor.service tourne)
```

⚠️ **Jamais pendant l'événement** : whisper.cpp est trop lent sur un Pi Zero 2 W pour tourner à côté de l'enregistrement en direct. Le script refuse de démarrer si `livre-dor.service` est actif (`--force` pour passer outre, déconseillé). À lancer la nuit sur le Pi, ou sur une machine plus puissante.

Seuls les `messages/*.wav` sans `.txt` sont traités : rééchantillonnage en 16 kHz mono par `ffmpeg` dans un fichier temporaire, transcription par `whisper-cli`, `.txt` écrit à côté du WAV. **Le fichier audio n'est jamais modifié.** Un échec sur un fichier est journalisé sans interrompre le reste du lot.
