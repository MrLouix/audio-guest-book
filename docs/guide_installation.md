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

  Carte son USB (via adaptateur OTG)
     ├── MIC IN  ◄── Micro électret (dans la cavité du combiné)
     └── Sortie stéréo (jack 3,5 mm)
            ├── Canal GAUCHE + masse ──► Écouteur d'origine
            └── Canal DROIT  + masse ──► Entrée PAM8403 ──► Haut-parleur externe
```

### 2.2 Étapes

1. **Crochet** → GPIO17 + GND, pull-up interne (`PUD_UP`). ⚠️ Vérifier au multimètre si le contact est normalement ouvert ou fermé au repos, et ajuster `HOOK_ACTIVE_STATE` en conséquence (voir §4 ci-dessous).
2. **Cadran, contact off-normal** → GPIO27 + GND. **Contact d'impulsions** → GPIO22 + GND. Même vérification multimètre pour `OFFNORMAL_ACTIF_LEVEL`.
3. **Micro électret** → entrée MIC de la carte son USB directement (jamais via le circuit hybride ou la capsule carbone d'origine).
4. **Sortie stéréo** : canal gauche vers l'écouteur (volume modéré), canal droit vers l'entrée du PAM8403 puis le haut-parleur externe (volume réglé au potentiomètre, voir §5).
5. **Alimentation** : Pi sur bloc 5 V ≥ 2,5 A ; PAM8403 alimenté depuis les broches 5V/GND du Pi.

---

## 3. Installation logicielle

Sur le Raspberry Pi (Raspberry Pi OS Lite, Bookworm), après avoir cloné ce dépôt dans `/home/pi/livre_dor/` :

```bash
cd /home/pi/livre_dor

# 1. Système : paquets, hostname mDNS, venv Python, arborescence, carte son
./scripts/install.sh

# 2. Fichiers audio : déposer les enregistrements des mariés dans audio_src/
#    (sonnerie.*, message_generique.*, message_0.* ... message_9.*), puis :
python3 src/prepare_audio.py
python3 src/prepare_audio.py --play-all   # vérification manuelle du panning au casque

# 3. Mots de passe du dashboard
python3 src/set_password.py         # mot de passe standard (remplace le défaut "livredor")
python3 src/set_admin_password.py   # optionnel : second mot de passe, connu de vous seul

# 4. Synchronisation Google Drive (une fois, avant l'événement)
rclone config                          # créer le remote "gdrive" (ou autre nom, cf. rclone_config.json)
sudo ./scripts/setup_rclone_systemd.sh

# 5. Transcription (optionnelle, à faire après l'événement, jamais avant)
./scripts/install_whisper.sh

# 6. Services systemd (démarrage automatique, redémarrage sur crash, watchdog)
sudo ./scripts/setup_systemd.sh
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

### À vérifier obligatoirement pour le matériel réel

| Paramètre | Défaut | Action requise |
|---|---|---|
| `SOUND_CARD` | `plughw:1,0` | Confirmer via `aplay -l` / `arecord -l` après branchement de la carte son USB |
| `HOOK_ACTIVE_STATE` | `LOW` | Vérifier au multimètre le sens logique du crochet (mode `--test`, §6) |
| `OFFNORMAL_ACTIF_LEVEL` | `LOW` | Vérifier au multimètre le sens logique du cadran (mode `--test`, §6) |

### Machine à états et audio

| Paramètre | Défaut | Description |
|---|---|---|
| `HOOK_PIN` / `DIAL_OFFNORMAL_PIN` / `DIAL_PULSE_PIN` | 17 / 27 / 22 | Broches GPIO (BCM) |
| `HOOK_DEBOUNCE_SEC` / `DIAL_DEBOUNCE_SEC` | 0.075 / 0.02 | Anti-rebond logiciel |
| `RING_INTERVAL_SEC` | 90 | Intervalle de la sonnerie périodique |
| `RING_ANSWER_GRACE_SEC` | 5 | Fenêtre « appel entrant » après la sonnerie |
| `MAX_RECORD_SEC` | 120 | Durée max d'un message invité |
| `AUDIO_PLAY_TIMEOUT_SEC` | 180 | Filet de sécurité contre un `aplay` bloqué |
| `SHORT_RECORDING_THRESHOLD_SEC` | 2.0 | Seuil « enregistrement très court » (conservé, jamais supprimé) |
| `RECORDING_HANGUP_CONFIRM_SEC` | 0.1 | Tolérance aux micro-coupures du crochet pendant l'enregistrement |
| `STATUS_HEARTBEAT_SEC` | 30 | Rafraîchissement de `status.json` en attente |

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
4. Vérifier qu'aucun son de la sonnerie ne fuite dans l'écouteur, et qu'aucun son de la tonalité/des messages ne sort du haut-parleur externe (panning correct, §4.2) — sinon revérifier le câblage des canaux gauche/droit.

---

## 6. Procédure de premier démarrage

1. `python3 src/livre_dor.py --test` → décrocher/raccrocher et tourner le cadran, vérifier que l'affichage reflète correctement chaque action. Ajuster `HOOK_ACTIVE_STATE`/`OFFNORMAL_ACTIF_LEVEL` si l'affichage semble inversé.
2. Démarrer les services (`sudo ./scripts/setup_systemd.sh`), puis `systemctl status livre-dor.service dashboard.service`.
3. Ouvrir `http://livredor.local:5000/` (ou `http://<IP>:5000/` si mDNS indisponible) → le dashboard doit demander le mot de passe.
4. Dérouler la [checklist de mise en service](checklist_mise_en_service.md) avant l'événement.

---

## 7. Sauvegarde à trois niveaux (§7.3)

1. **microSD locale** (source de vérité, jamais purgée automatiquement).
2. **Google Drive** via `rclone copy` (jamais `sync`, jamais de suppression).
3. **Copie manuelle USB** possible après l'événement, en complément.
