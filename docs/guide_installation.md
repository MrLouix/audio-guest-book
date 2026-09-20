# Guide d'installation — Livre d'or téléphonique (Socotel S63)

Ce guide couvre l'assemblage matériel, l'installation logicielle et la configuration nécessaires pour passer du dépôt Git à un appareil prêt pour l'événement. Il complète (sans le remplacer) le [cahier des charges](specification_livre_dor_telephonique.md) : les références `§X` renvoient à ses sections.

Une fois ce guide suivi, dérouler la [checklist de mise en service](checklist_mise_en_service.md) avant l'événement.

---

## 1. Matériel requis (§2)

| # | Composant | Remarque |
|---|---|---|
| 1 | Téléphone Socotel S63 | Boîtier, combiné, crochet, cadran |
| 2 | Raspberry Pi Zero 2 W | + carte microSD ≥ 16 Go classe A1 |
| 3 | Carte son USB stéréo (entrée micro + sortie casque) | Le Pi Zero n'a pas d'audio analogique natif |
| 4 | Adaptateur micro-USB OTG | Pour brancher la carte son |
| 5 | Micro électret (~2 €) | Capture voix des invités |
| 6 | Écouteur d'origine du combiné | Diffusion tonalité/messages/bip |
| 7 | Mini-ampli PAM8403 (avec potentiomètre) | Amplifie le haut-parleur externe |
| 8 | Haut-parleur externe (4–8 Ω, 3 W) | Diffusion de la sonnerie |
| 9 | Alimentation 5 V / ≥ 2,5 A | Qualité importante (§7.5) : évite les brownouts |
| 10 | Fils Dupont, gaine thermorétractable | Câblage interne |

La capsule carbone d'origine et le circuit hybride du téléphone ne sont **pas** utilisés électriquement (conservés pour l'esthétique uniquement).

---

## 2. Câblage (§3)

### 2.1 Vue d'ensemble

```
  [Crochet combiné] ──── GPIO17 (BCM, phys. 11)  + GND
  [Cadran: off-normal] ── GPIO27 (BCM, phys. 13)  + GND
  [Cadran: impulsions] ── GPIO22 (BCM, phys. 15)  + GND

  IQaudio Codec Zero (HAT, carte 1)
     ├── AUX IN (gauche) ◄── Micro ADA1063 (dans la cavité du combiné)
     ├── LINE OUT ──► Entrée PAM8403 ──► Haut-parleur MONO de sonnerie
     │                 (le haut-parleur lit la piste GAUCHE)
     └── CASQUE (jack 3,5 mm, stéréo)
            ├── Canal GAUCHE + masse ──► Écouteur d'origine du combiné
            └── Canal DROIT  + masse ──► Écouteur secondaire
```

Le codec n'alimente pas les deux sorties en même temps : il est commuté avant
chaque lecture (line out pour la sonnerie, casque pour tout le reste). Les
fichiers joués sont donc stéréo avec **les deux pistes identiques** — l'écouteur
du combiné et l'écouteur secondaire entendent la même chose, au même niveau.

### 2.2 Étapes

1. **Crochet** → GPIO17 + GND, pull-up interne (`PUD_UP`). ⚠️ Vérifier au multimètre si le contact est normalement ouvert ou fermé au repos, et ajuster `HOOK_ACTIVE_STATE` en conséquence (voir §4 ci-dessous).
2. **Cadran, contact off-normal** → GPIO27 + GND. **Contact d'impulsions** → GPIO22 + GND. Même vérification multimètre pour `OFFNORMAL_ACTIF_LEVEL`.
3. **Micro ADA1063** → entrée **Aux gauche** du Codec Zero directement (jamais via le circuit hybride ou la capsule carbone d'origine).
4. **Line out** → entrée du PAM8403 puis haut-parleur mono de sonnerie (volume réglé au potentiomètre, voir §5). **Sortie casque** : canal gauche vers l'écouteur du combiné, canal droit vers l'écouteur secondaire (volumes modérés, identiques).
5. **Alimentation** : Pi sur bloc 5 V ≥ 2,5 A ; PAM8403 alimenté depuis les broches 5V/GND du Pi.

---

## 3. Installation logicielle

Sur le Raspberry Pi (Raspberry Pi OS Lite, Bookworm), après avoir cloné ce dépôt dans `/home/pi/livre_dor/` :

```bash
cd /home/pi/livre_dor

# 1. Système : paquets, hostname mDNS, venv Python, arborescence, carte son
./scripts/install.sh

# 2. Fichiers audio : déposer les sources dans audio_src/, avec leur nom
#    d'origine (mp3, m4a, wav...). Le fichier joué pour chaque rôle se choisit
#    ensuite dans le dashboard (/settings), par liste déroulante.
#    À défaut de choix explicite, l'ancienne convention de nommage s'applique
#    (sonnerie.*, message_generique.*, message_0.* ... message_9.*, et
#    éventuellement aucun_message.* pour le mode restitution, §5.7).
python3 src/prepare_audio.py              # conversion 48 kHz, 16 bits, stéréo L = R
python3 src/prepare_audio.py --play-all   # rejoue chaque fichier sur SA sortie (vérif. câblage)

# 3. Mots de passe du dashboard
python3 src/set_password.py         # mot de passe standard (remplace le défaut "livredor")
python3 src/set_admin_password.py   # optionnel : second mot de passe, connu de vous seul

# 4. Synchronisation Google Drive (une fois, avant l'événement)
rclone config                          # créer le remote "gdrive" (ou autre nom, cf. rclone_config.json)
sudo ./scripts/setup_rclone_systemd.sh
# Puis, depuis le dashboard (/rclone) : renseigner les deux dossiers Drive
# (celui des enregistrements et celui des sources, DISTINCTS), activer la
# synchronisation bidirectionnelle et lancer « Réinitialiser la synchronisation
# bidirectionnelle » une fois — ce premier passage peut être long, il se fait
# maintenant et pas pendant l'événement.
python3 src/rclone_sync.py --resync    # équivalent en ligne de commande

# 5. Transcription (optionnelle, à faire après l'événement, jamais avant)
./scripts/install_whisper.sh

# 6. Services systemd (démarrage automatique, redémarrage sur crash, watchdog)
sudo ./scripts/setup_systemd.sh
```

### Après l'événement : mode restitution (§5.7)

Le téléphone peut devenir un lecteur des messages laissés par les invités :
décrocher, composer au cadran le numéro d'un message (4 chiffres au maximum),
l'écouter. Ni sonnerie, ni enregistrement possible dans ce mode.

La bascule se fait depuis le dashboard, page **Mode** (`http://livredor.local:5000/mode`) :
elle est prise en compte en moins d'une seconde, sans redémarrer le service. Aucun
accès SSH n'est nécessaire. Vérification préalable de la logique, sans matériel :

```bash
python3 src/restitution_test.py
```

### Dépendances logicielles (§10)

- OS : Raspberry Pi OS Lite (Bookworm), NetworkManager (`nmcli`), `avahi-daemon` (mDNS), systemd.
- Paquets système : `alsa-utils`, `rclone`, `ffmpeg`, `python3-venv` (tous installés par `scripts/install.sh`).
- Python (venv, `requirements.txt`) : `RPi.GPIO`, `Flask`, `qrcode[pil]`, `pydub`, `werkzeug`.
- Front vendorisé : `jsQR.min.js` (déjà présent dans `static/`, aucune installation requise).
- Optionnel post-événement : `whisper.cpp` + modèle `tiny-q5_0` (`scripts/install_whisper.sh`).

---

## 4. Configuration (§9)

Tous les paramètres ci-dessous sont centralisés dans `src/config.py` et surchargeables par variable d'environnement du même nom (utile pour un service systemd : `Environment=NOM=valeur` dans le fichier `.service`, sans jamais modifier le code).

Une partie d'entre eux est également réglable depuis la page **Paramètres** du dashboard (`/settings`, réservée au mot de passe administrateur) : ceux déclarés dans `MODIFIABLE_PARAMS`. Ordre de précédence, du plus fort au plus faible :

1. la **variable d'environnement** du même nom — le réglage figé de l'installation ;
2. **`custom_config.json`**, écrit par la page Paramètres ;
3. la **valeur par défaut** du code.

Un paramètre fixé par variable d'environnement n'est donc pas modifiable depuis le dashboard. Et comme tout est résolu au démarrage du processus, et que `livre_dor.py` tourne dans un autre service que le dashboard, **une modification faite depuis `/settings` ne prend effet qu'après `sudo systemctl restart livre-dor`** — la page le signale à l'enregistrement. Seule exception : le mode mariage/restitution, qui bascule à chaud par `mode_config.json` et sa propre page `/mode` (§5.7).

### À vérifier obligatoirement pour le matériel réel

| Paramètre | Défaut | Action requise |
|---|---|---|
| `SOUND_CARD` | `plughw:1,0` | Confirmer via `aplay -l` / `arecord -l` après branchement de la carte son USB |
| `HOOK_ACTIVE_STATE` | `LOW` | Vérifier au multimètre le sens logique du crochet (mode `--test`, §6) |
| `OFFNORMAL_ACTIF_LEVEL` | `LOW` | Vérifier au multimètre le sens logique du cadran (mode `--test`, §6) |
| `PULSE_ACTIF_LEVEL` | suit `OFFNORMAL_ACTIF_LEVEL` | Sens logique du **contact d'impulsions**, qui n'est pas forcément celui de l'off-normal : un contact « normalement fermé » tient la broche au niveau bas au repos et la relâche à chaque impulsion, donc `HIGH`. À lire avec `python3 tests/scope_impulsions.py --niveaux` : cadran immobile, la broche doit être au niveau **opposé** à ce réglage |

### Machine à états et audio

| Paramètre | Défaut | Description |
|---|---|---|
| `HOOK_PIN` / `DIAL_OFFNORMAL_PIN` / `DIAL_PULSE_PIN` | 17 / 27 / 22 | Broches GPIO (BCM) |
| `HOOK_DEBOUNCE_SEC` / `DIAL_DEBOUNCE_SEC` | 0.075 / 0.02 | Anti-rebond logiciel |
| `GPIO_ECHANTILLONNAGE_HZ` | 1000 | Cadence de relecture des trois broches pendant une rotation. Les entrées ne sont pas lues par interruption mais échantillonnées, seul moyen de lire un contact usé |
| `GPIO_ECHANTILLONNAGE_REPOS_HZ` | 50 | Cadence au repos, c'est-à-dire l'essentiel du temps. Le thread tourne en permanence : c'est cette valeur-là qui décide de ce que le service consomme. La mettre égale à `GPIO_ECHANTILLONNAGE_HZ` désactive l'adaptation. `python3 tests/scope_impulsions.py --charge` mesure le coût des deux cadences sur la machine |
| `GPIO_ACTIVITE_SEC` | 2.0 | Durée de maintien en cadence rapide après le dernier changement de niveau d'une broche |
| `PULSE_MIN_ACTIF_SEC` | 0.005 | Niveau actif franc qui ouvre une impulsion. Doit rester bien sous la plus courte impulsion réelle (~33 ms) |
| `PULSE_MIN_REPOS_SEC` | 0.025 | Repos franc qui clôt l'impulsion. **Le réglage décisif sur un contact usé** : plus long que la plus longue micro-coupure du grésillement, plus court que le plus court repos réel. `python3 tests/scope_impulsions.py --reel` balaie cette valeur et affiche le palier — réglez au centre |
| `HOOK_CONFIRM_SEC` / `OFFNORMAL_CONFIRM_SEC` | suivent `HOOK_DEBOUNCE_SEC` / `DIAL_DEBOUNCE_SEC` | Durée de maintien confirmant un changement d'état du crochet et du contact off-normal |
| `RING_INTERVAL_SEC` | 90 | Intervalle de la sonnerie périodique |
| `RING_ANSWER_GRACE_SEC` | 5 | Fenêtre « appel entrant » après la sonnerie |
| `MAX_RECORD_SEC` | 120 | Durée max d'un message invité |
| `AUDIO_PLAY_TIMEOUT_SEC` | 180 | Filet de sécurité contre un `aplay` bloqué |
| `SHORT_RECORDING_THRESHOLD_SEC` | 2.0 | Seuil « enregistrement très court » (conservé, jamais supprimé) |
| `RECORDING_HANGUP_CONFIRM_SEC` | 0.1 | Tolérance aux micro-coupures du crochet pendant l'enregistrement |
| `STATUS_HEARTBEAT_SEC` | 120 | Rafraîchissement de `status.json` en attente. Chaque écriture coûte un bloc neuf sur la carte SD : ~9 Mo/jour à 120 s, contre ~36 Mo/jour à 30 s. Ne pas approcher `WATCHDOG_STALE_AFTER_SEC` (300 s) |

### Mode restitution (§5.7)

| Paramètre | Défaut | Description |
|---|---|---|
| `MODE_RESTITUTION` | `False` | Mode au **premier démarrage** seulement : ensuite `mode_config.json` fait foi (bascule via la page `/mode`) |
| `RESTITUTION_DIGITS_MAX` | 4 | Nombre max de chiffres du numéro de message ; au dernier chiffre la saisie se ferme aussitôt. Réglable depuis `/settings` |
| `RESTITUTION_INTERDIGIT_SEC` | 3.0 | Silence du cadran validant un numéro plus court (« 1 » puis attente). Réglable depuis `/settings` |
| `MODE_RELOAD_SEC` | 1.0 | Durée de validité du mode en mémoire avant relecture de `mode_config.json`. Plus haut = moins d'appels système, bascule un peu moins réactive |
| `RESTITUTION_SOUND_CARD` | = `SOUND_CARD` | Périphérique ALSA de lecture des messages des invités. Échappatoire de routage : définir un périphérique ALSA `route` et le pointer ici, sans modification de code |
| `AUDIO_OUTPUT_SONNERIE` / `AUDIO_OUTPUT_COMBINE` | `lineout` / `headphone` | Sortie du codec pour la sonnerie et pour les écouteurs (§4.1). Modifiables depuis `/settings` |
| `AUDIO_RATE_HZ` / `AUDIO_CHANNELS` | 48000 / 2 | Format commun lecture et capture (RNNoise, full duplex) |
| `RCLONE_SOURCES_FOLDER` | `MariageGuestBookSources` | Dossier Drive de `audio_src/`, synchronisé **dans les deux sens**. Doit être distinct de celui des enregistrements |

### Dashboard & authentification

| Paramètre | Défaut | Description |
|---|---|---|
| `WEB_PORT` / `WEB_PORT_MAX_ATTEMPTS` | 5000 / 1 | Port fixe, pas de repli automatique |
| `USE_MDNS` / `MDNS_HOSTNAME` | True / `livredor` | URL stable `http://livredor.local:5000/` |
| `QR_LABEL_SIZE_MM` | 45 | Taille par défaut de l'étiquette imprimable |
| `SESSION_LIFETIME_HOURS` | 12 | Durée de session dashboard |
| `LOGIN_FAILED_ATTEMPTS_THRESHOLD` / `_DELAY_SEC` / `_DELAY_MAX_SEC` | 3 / 1.0 / 10.0 | Anti-brute-force du login |

### Réseau WiFi/AP

| Paramètre | Défaut | Description |
|---|---|---|
| `AP_CONNECTION_NAME` / `AP_SSID` / `AP_IP` | `GuestbookAP` / `Livre-dor-Mariage` / `192.168.4.1` | Point d'accès de secours |
| `AP_PASSWORD` | `livredormariage` | **À changer à l'installation**, comme le mot de passe dashboard |
| `CONNECT_TIMEOUT_SEC` | 15 | Timeout par tentative de connexion WiFi |
| `WIFI_SIGNAL_MIN` | 25 | Signal minimal (%) pour tenter/conserver un réseau |
| `WIFI_FAIL_THRESHOLD` / `WIFI_BLACKLIST_MIN` | 3 / 10 | Anti-« wifi zombie » |

### Google Drive & stockage

| Paramètre | Défaut | Description |
|---|---|---|
| `RCLONE_REMOTE` / `RCLONE_FOLDER` / `RCLONE_INTERVAL_MIN` | `gdrive` / `MariageGuestBook` / 5 | Modifiables aussi depuis `/rclone` |
| `DISK_WARNING_MB` / `DISK_CRITICAL_MB` | 500 / 100 | Seuils d'alerte / refus d'enregistrement |
| `WATCHDOG_STALE_AFTER_SEC` | 300 | Délai avant redémarrage par le watchdog |

### Transcription (optionnelle)

| Paramètre | Défaut | Description |
|---|---|---|
| `WHISPER_BINARY` / `WHISPER_MODEL_PATH` | `whisper-cli` / `whisper.cpp/models/ggml-tiny-q5_0.bin` | Chemins produits par `scripts/install_whisper.sh` |
| `WHISPER_LANGUAGE` | `fr` | Langue de transcription |

---

## 5. Réglage du potentiomètre PAM8403

1. Fichiers audio préparés (`python3 src/prepare_audio.py`) et carte son branchée.
2. Couper le volume du potentiomètre au minimum avant la première mise sous tension de l'ampli (évite un pic sonore).
3. Jouer la sonnerie seule (`python3 src/audio_io.py play audio/ring_out.wav`) et monter progressivement le potentiomètre jusqu'au volume souhaité pour attirer l'attention sans être agressif.
4. Vérifier que la sonnerie ne sort **que** du haut-parleur de sonnerie, et que la tonalité et les messages sortent des **deux** écouteurs, au même niveau, sans rien laisser passer par le haut-parleur (§4.1) :

```bash
./scripts/audio-setup.sh lineout   && aplay -D plughw:1,0 audio/ring_out.wav
./scripts/audio-setup.sh headphone && aplay -D plughw:1,0 audio/message_generique.wav
./scripts/audio-setup.sh status    # ALC off, sortie active cohérente
```

Si un écouteur reste muet, c'est le câblage du jack casque qu'il faut revoir — les fichiers générés portent le même signal sur les deux pistes. Si rien ne sort du tout, vérifier d'abord `./scripts/audio-setup.sh status`.

---

## 6. Procédure de premier démarrage

1. `python3 src/livre_dor.py --test` → décrocher/raccrocher et tourner le cadran, vérifier que l'affichage reflète correctement chaque action. Ajuster `HOOK_ACTIVE_STATE`/`OFFNORMAL_ACTIF_LEVEL`/`PULSE_ACTIF_LEVEL` si l'affichage semble inversé.
1. Si les chiffres composés sont lus de travers : `python3 tests/scope_impulsions.py --reel`, qui échantillonne les broches à 10 kHz, affiche la forme du signal et rend un verdict (câblage, polarité, anti-rebond). `--niveaux` donne le moniteur de niveaux en continu.
2. Démarrer les services (`sudo ./scripts/setup_systemd.sh`), puis `systemctl status livre-dor.service dashboard.service`.
3. Ouvrir `http://livredor.local:5000/` (ou `http://<IP>:5000/` si mDNS indisponible) → le dashboard doit demander le mot de passe.
4. Dérouler la [checklist de mise en service](checklist_mise_en_service.md) avant l'événement.

---

## 7. Sauvegarde à trois niveaux (§7.3)

1. **microSD locale** (source de vérité, jamais purgée automatiquement).
2. **Google Drive** via `rclone copy` (jamais `sync`, jamais de suppression).
3. **Copie manuelle USB** possible après l'événement, en complément.
