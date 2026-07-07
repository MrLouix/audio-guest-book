# Spécification technique — Livre d'or téléphonique (Socotel S63)

**Version :** 1.0 — 6 juillet 2026
**Objet :** Document de référence unique reprenant l'intégralité du projet, destiné à servir de cahier des charges pour l'implémentation du code. Aucun code n'est inclus : seuls les comportements, configurations, interfaces et exigences de fiabilité sont spécifiés.

---

## 1. Vue d'ensemble

### 1.1 Concept

Un téléphone à cadran vintage **Socotel S63** est transformé en livre d'or automatique pour un mariage. Les invités décrochent le combiné, entendent une tonalité authentique, composent un chiffre sur le cadran, écoutent un message pré-enregistré des mariés, puis laissent leur propre message vocal après un bip. Les messages sont horodatés, sauvegardés localement et synchronisés vers Google Drive. Un tableau de bord web auto-hébergé permet de superviser l'appareil pendant l'événement.

### 1.2 Parcours invité (expérience nominale)

1. **Au repos**, le téléphone sonne périodiquement (sonnerie diffusée par haut-parleur externe) pour attirer l'attention des invités. La sonnerie peut aussi être déclenchée à distance depuis le dashboard.
2. **Décroché** → la sonnerie s'arrête immédiatement. Deux scénarios selon le contexte du décroché :
   - **Scénario « appel entrant »** : si l'invité décroche **pendant la sonnerie** (ou dans une courte fenêtre de grâce après sa fin, `RING_ANSWER_GRACE_SEC`, défaut 5 s), le système simule un vrai appel : **aucune tonalité, aucun chiffre à composer** — un message des mariés est joué **au hasard** dans l'écouteur, suivi du bip, puis l'enregistrement démarre comme d'habitude (étapes 5–6).
   - **Scénario « appel sortant » (nominal)** : hors sonnerie, une **tonalité d'invitation à numéroter** (mélange de sinusoïdes 440 Hz + 480 Hz, comme un vrai téléphone) est jouée dans l'écouteur.
3. *(Scénario sortant uniquement)* L'invité **compose UN chiffre** sur le cadran rotatif → la tonalité s'arrête **dès la première impulsion détectée** (fidèle au comportement d'un vrai téléphone).
4. *(Scénario sortant uniquement)* Le **message associé au chiffre composé** est joué dans l'écouteur. Si aucun message n'est attribué à ce chiffre → un **message générique** est joué à la place.
5. Un **bip** retentit, puis **l'enregistrement démarre** (micro électret caché dans le combiné).
6. L'invité **raccroche** → l'enregistrement s'arrête, le fichier WAV horodaté est sauvegardé, le système revient à l'état d'attente.

Règles transverses du parcours :
- **Toute lecture audio doit être interruptible** : si l'invité raccroche pendant la tonalité, le message ou le bip, la lecture s'arrête immédiatement et le système revient en attente.
- **Durée d'enregistrement plafonnée** (`MAX_RECORD_SEC`, défaut 120 s) pour éviter les fichiers infinis si un invité oublie de raccrocher.
- Chiffre non attribué → fallback message générique (jamais de silence bloquant).
- **Tirage aléatoire (appel entrant)** : le message est choisi au hasard parmi **tous les messages des mariés disponibles** (`message_N.wav` + `message_generique.wav`). Éviter de rejouer deux fois de suite le même message (mémoriser le dernier tiré). Si l'invité raccroche pendant le message ou le bip, aucun enregistrement n'est créé et le système revient en attente, comme dans le scénario nominal.

---

## 2. Liste du matériel (électronique)

| # | Composant | Rôle | Remarque |
|---|---|---|---|
| 1 | Téléphone Socotel S63 | Boîtier, combiné, crochet, cadran | Acheté 19 € |
| 2 | Raspberry Pi Zero 2 W | Cerveau du montage | 512 Mo RAM, quad-core 1 GHz, WiFi + Bluetooth intégrés |
| 3 | Carte microSD (≥ 16 Go, classe A1) | OS + enregistrements | Prévoir marge : 1 h de WAV mono 44,1 kHz ≈ 300 Mo |
| 4 | Carte son USB stéréo (entrée mic + sortie casque) | Toute l'entrée/sortie audio | Le Pi Zero n'a pas d'audio analogique natif |
| 5 | Adaptateur micro-USB OTG | Brancher la carte son sur le Pi Zero | |
| 6 | Micro électret (~2 €) | Capture de la voix des invités | Caché dans la cavité du combiné |
| 7 | Écouteur d'origine du combiné | Diffusion tonalité + messages des mariés | Impédance 150–300 Ω, compatible sortie casque |
| 8 | Mini-ampli classe D **PAM8403** (avec potentiomètre) | Amplifie le canal haut-parleur | Alimenté en 5 V |
| 9 | Petit haut-parleur externe (4–8 Ω, 3 W) | Diffusion de la sonnerie | Caché dans/sous le boîtier |
| 10 | Crochet d'origine (hook switch) | Détection décroché/raccroché | Réutilisé tel quel |
| 11 | Cadran rotatif d'origine | Sélection du message (1 chiffre) | 2 contacts : impulsions + « off-normal » |
| 12 | Résistance de protection GPIO (optionnelle, ~1 kΩ) | Protection des entrées GPIO | Selon l'état du crochet/cadran |
| 13 | Alimentation 5 V / ≥ 2,5 A (micro-USB) | Alimente Pi + ampli | Qualité importante : éviter les brownouts |
| 14 | Fils Dupont / câblage, gaine thermorétractable | Connexions internes | |

**Composants d'origine non utilisés électriquement (conservés pour l'esthétique) :**
- La capsule **carbone** du micro d'origine reste en place dans le combiné mais **n'est pas connectée** (elle nécessite un courant de polarisation DC incompatible avec une carte son USB).
- Le **circuit hybride interne** du téléphone est **entièrement contourné** : aucun signal audio ne passe par lui.
- La cloche mécanique d'origine n'est pas utilisée (la génération de tension AC de sonnerie ~48 V est évitée) ; la sonnerie est un fichier audio joué sur le haut-parleur externe.

---

## 3. Schéma de câblage

### 3.1 Vue d'ensemble

```
                            ┌──────────────────────────┐
                            │   Raspberry Pi Zero 2 W  │
                            │                          │
  [Crochet combiné] ────────│ GPIO17 (BCM, phys. 11)   │
        └───────────────────│ GND                      │
                            │                          │
  [Cadran: off-normal] ─────│ GPIO27 (BCM, phys. 13)   │
        └───────────────────│ GND                      │
                            │                          │
  [Cadran: impulsions] ─────│ GPIO22 (BCM, phys. 15)   │
        └───────────────────│ GND                      │
                            │                          │
                            │ USB (via OTG) ───────────│──► [Carte son USB]
                            │ 5V ──────────────────────│──► [PAM8403 VCC]
                            │ GND ─────────────────────│──► [PAM8403 GND]
                            └──────────────────────────┘

  [Carte son USB]
     ├── MIC IN  ◄── [Micro électret caché dans le combiné]
     └── SORTIE STÉRÉO (jack 3,5 mm)
            ├── Canal GAUCHE + masse ──► [Écouteur d'origine du combiné]
            └── Canal DROIT  + masse ──► [Entrée PAM8403] ──► [Haut-parleur externe]
```

### 3.2 Détail par connexion

**A. Crochet (hook switch) → GPIO17**
- Une extrémité du contact → GPIO17, l'autre → GND.
- Entrée configurée avec **pull-up interne** (`PUD_UP`).
- ⚠️ **À vérifier au multimètre avant câblage** : selon le modèle, le contact est normalement fermé ou ouvert au repos. Le paramètre logiciel `HOOK_ACTIVE_STATE` (état GPIO = décroché) doit être ajusté en conséquence.

**B. Cadran rotatif → GPIO27 et GPIO22**
- **Contact « off-normal »** → GPIO27 : fermé/ouvert dès que le cadran quitte sa position de repos ; signale « numérotation en cours ».
- **Contact d'impulsions** → GPIO22 : génère N ouvertures/fermetures pour le chiffre N (le « 0 » = 10 impulsions).
- Les deux avec pull-up interne, l'autre borne à GND.
- Le sens logique des deux contacts doit aussi être vérifié au multimètre (paramètre `OFFNORMAL_ACTIF_LEVEL`).

**C. Micro électret → entrée MIC de la carte son USB**
- Câblé **directement** (signal + masse) sur l'entrée micro. L'entrée MIC des cartes son USB fournit la polarisation nécessaire à un électret.
- **Ne jamais repasser par le circuit hybride ni la capsule carbone d'origine.**
- Positionner l'électret dans la cavité micro du combiné, derrière la grille.

**D. Sortie stéréo → deux destinations séparées (astuce clé du projet)**
- **Canal gauche** → écouteur d'origine du combiné (tonalité, messages des mariés, bip). Volume modéré.
- **Canal droit** → entrée du PAM8403 → haut-parleur externe (sonnerie uniquement). Volume élevé, réglable au potentiomètre.
- Cela permet d'avoir **une seule carte son** avec deux sorties indépendantes, à condition que tous les fichiers audio soient pré-traités (voir §4.2).

**E. Alimentation**
- Pi alimenté par bloc 5 V ≥ 2,5 A.
- PAM8403 alimenté depuis les broches 5 V/GND du Pi (consommation faible à volume raisonnable).

---

## 4. Architecture audio

### 4.1 Périphérique

- Périphérique ALSA de la carte son USB, identifié une fois avec `aplay -l` et `arecord -l`. Valeur type : **`plughw:1,0`** (paramètre `SOUND_CARD` / `CARTE_SON`).
- Lecture via `aplay`, enregistrement via `arecord` (paquet `alsa-utils`) pilotés en sous-processus.

### 4.2 Pré-traitement des fichiers (étape unique, avant l'événement)

Un script de préparation (équivalent de `prepare_audio.py`, à recoder) transforme chaque fichier source mono/stéréo en fichier **stéréo pané à 100 % sur un seul canal**, avec gain ajustable par fichier (bibliothèque type pydub) :

| Fichier source | Canal cible | Gain indicatif | Usage |
|---|---|---|---|
| Sonnerie | **Droit** | 0 dB | Haut-parleur externe |
| Message(s) des mariés (un par chiffre + un générique) | **Gauche** | −6 à −12 dB | Écouteur |
| Tonalité (440 + 480 Hz, générée par synthèse) | **Gauche** | −12 dB | Écouteur |
| Bip avant enregistrement | **Gauche** | −6 dB | Écouteur |

L'équilibre final des volumes se règle par le couple **gain logiciel** (pré-traitement) + **potentiomètre physique** du PAM8403.

### 4.3 Enregistrements

- Format : **WAV**, mono, 44,1 kHz (ou 22,05 kHz si l'espace disque est un souci).
- Nommage : horodaté, ex. `message_YYYY-MM-DD_HH-MM-SS.wav`, éventuellement suffixé du chiffre composé.
- Dossier : `/home/pi/livre_dor/messages/` (cf. arborescence §8).

---

## 5. Composants logiciels

Le système est composé de **4 unités logicielles** indépendantes communiquant par fichiers, plus des services systemd.

### 5.1 Script principal `livre_dor.py` (machine à états)

**États :** `attente` → `decroche` (tonalité) → `numerotation` → `lecture_message` → `enregistrement` → retour `attente`. Branche parallèle « appel entrant » : `sonnerie` → décroché → `appel_repondu` (message aléatoire, sans tonalité ni cadran) → `enregistrement`. État spécial `erreur`.

**Logique « appel entrant » (simulation d'un vrai appel) :**
- Un drapeau interne « sonnerie récente » est actif **pendant** la lecture du fichier de sonnerie **et pendant `RING_ANSWER_GRACE_SEC` secondes** (défaut 5 s) après sa fin — que la sonnerie soit périodique ou déclenchée à distance via `ring_trigger`.
- Si le décroché survient pendant que ce drapeau est actif → transition directe vers `appel_repondu` : la sonnerie est coupée immédiatement, **aucune tonalité n'est jouée, le cadran est ignoré**, un message des mariés est tiré au hasard (jamais deux fois de suite le même) et joué dans l'écouteur, puis bip → enregistrement → sauvegarde au raccroché, identique au flux nominal.
- Si le décroché survient hors de cette fenêtre → flux nominal (tonalité + cadran).
- Toute impulsion du cadran reçue en `appel_repondu` est ignorée (loguée en debug), sans effet sur la lecture ni l'enregistrement.

**Responsabilités :**
- Lecture des 3 GPIO (crochet, off-normal, impulsions) avec **anti-rebond logiciel (debounce)** sur les impulsions du cadran.
- Comptage des impulsions pendant que le contact off-normal est actif ; validation du chiffre au retour du cadran au repos.
- Mapping chiffre → fichier message ; fallback message générique.
- Lecture interruptible (surveillance du crochet toutes les ~100 ms pendant l'`aplay`, kill du sous-processus au raccroché).
- Enregistrement `arecord` arrêté au raccroché ou à `MAX_RECORD_SEC`.
- **Sonnerie périodique** en attente : joue le fichier sonnerie toutes les `RING_INTERVAL_SEC` (défaut 90 s), interrompue immédiatement au décroché. Vérifie aussi à chaque itération l'existence du fichier drapeau `ring_trigger` (déclenchement à distance depuis le dashboard) : s'il existe → sonner immédiatement puis supprimer le fichier.
- Écriture continue de **`status.json`** (état courant + horodatage) pour le dashboard.
- Journalisation dans `logs/livre_dor.log`.
- **Mode `--test`** : affiche en direct l'état des 3 broches GPIO pour valider le câblage et les sens logiques (décrocher, tourner le cadran, observer).

### 5.2 Dashboard web (Flask) `dashboard_app.py`

Serveur Flask écoutant sur `0.0.0.0:5000`, accessible via `http://livredor.local:5000/`.

**Pages et API :**

| Route | Méthode | Fonction |
|---|---|---|
| `/` | GET | Tableau de bord : état en direct (attente/décroché/enregistrement/erreur avec code couleur), nombre de messages, mode réseau (wifi ou AP) + IP, dernières lignes de log (auto-rafraîchi : statut ~4 s, logs ~8 s, avec préservation du scroll) |
| `/api/status` | GET | JSON : état, dernière MAJ, mode réseau, IP, nb messages |
| `/api/logs` | GET | JSON : ~150 dernières lignes du log |
| `/api/messages/count` | GET | JSON : nombre de messages enregistrés |
| `/api/ring` | POST | Crée le fichier drapeau `ring_trigger` → sonnerie immédiate |
| `/qr` | GET | Deux QR codes : (a) QR WiFi avec identifiants de l'AP (affiché uniquement en mode AP), (b) QR de l'URL du dashboard |
| `/qr/label` | GET | Étiquette imprimable dimensionnée en millimètres (défaut 45 mm, paramètre `?taille=NN`) à coller sur le téléphone |
| `/wifi` | GET | Page permettant de photographier le QR code WiFi d'un lieu ; décodage **100 % côté client** avec `jsQR.min.js` **servi localement** (vendorisé via npm, fichier statique Flask — aucune connexion internet requise) ; confirmation du SSID détecté par l'utilisateur |
| `/api/wifi/add` | POST | Crée le profil WiFi via `nmcli` et tente la connexion. **Avertissement affiché dans l'UI** : si le Pi est en mode AP au moment de la confirmation, le téléphone de l'utilisateur perd sa connexion en cours de requête (une seule antenne, impossible de tenir les deux réseaux) ; se reconnecter ensuite au nouveau WiFi et rouvrir `livredor.local` |
| `/rclone` | GET/POST | Configuration de la synchronisation Google Drive (voir §5.4) |

**QR / adressage stable :** `USE_MDNS = True`, `MDNS_HOSTNAME = "livredor"` → tous les QR codes encodent `http://livredor.local:5000/` (jamais une IP brute), ce qui survit aux bascules wifi/AP. Prérequis sur le Pi : `hostnamectl set-hostname livredor` + redémarrage d'`avahi-daemon`.

**Port fixe :** `WEB_PORT = 5000`, `WEB_PORT_MAX_ATTEMPTS = 1` (pas de repli de port automatique — décision projet : garder une adresse fixe et prévisible pour l'étiquette imprimée). Si le port est pris, le service s'arrête avec un message clair dans les logs plutôt que de basculer silencieusement. Le port réellement utilisé est écrit dans `active_port.txt`.

**Génération QR :** bibliothèque `qrcode[pil]`.

### 5.3 Bascule réseau `wifi_or_ap.sh` (+ timer systemd 30 s)

Comportement (version robuste, décidée en cours de projet) :
1. Si déjà connecté à un **vrai** wifi (≠ profil AP) → **surveillance de qualité** (voir ci-dessous) ; si la connexion est saine, ne rien faire.
2. Sinon : rescan (`nmcli device wifi rescan`), lister **tous** les profils connus dont le SSID est actuellement visible, les **trier par puissance de signal décroissante** (champ `SIGNAL` de `nmcli device wifi list`), puis les essayer dans cet ordre (timeout `CONNECT_TIMEOUT = 15 s` chacun) jusqu'à connexion réelle. Les réseaux dont le signal est inférieur à `WIFI_SIGNAL_MIN` (défaut 25 %) sont **ignorés d'office** — mieux vaut l'AP qu'un wifi inutilisable. Les réseaux temporairement blacklistés (voir ci-dessous) sont également écartés.
3. Si aucun ne fonctionne → repli sur le **point d'accès local** : profil nmcli `GuestbookAP`, SSID diffusé **« Livre-dor-Mariage »**, IP fixe **192.168.4.1**.
4. Le point d'accès n'est coupé qu'au moment où une vraie tentative de connexion commence (pour ne pas laisser le Pi injoignable entre les deux).
5. Journalisation de chaque décision dans `logs/reseau.log`.

**Surveillance de qualité de la connexion active (anti-« wifi zombie ») :**

Problème couvert : le Pi peut être connecté au seul réseau disponible mais avec un signal trop faible ou une liaison instable — il est alors « connecté » aux yeux de nmcli tout en étant **injoignable** en pratique, sans jamais déclencher le repli AP. Pour l'éviter, à chaque exécution du timer (30 s), quand un vrai wifi est actif :

- **Contrôle de signal** : lire la puissance du réseau en cours (`nmcli -f IN-USE,SIGNAL device wifi`) ; sous `WIFI_SIGNAL_MIN`, le contrôle est compté comme échec.
- **Contrôle de liaison** : ping de la passerelle (1–2 paquets, timeout court) ; échec du ping = contrôle en échec. (La passerelle plutôt qu'internet : le dashboard ne dépend que du réseau local.)
- Un **compteur d'échecs consécutifs** est persisté (fichier d'état, ex. `/run/livre_dor/wifi_health`). Un contrôle réussi le remet à zéro.
- Après **`WIFI_FAIL_THRESHOLD` échecs consécutifs** (défaut 3, soit ~90 s de connexion dégradée) : le SSID fautif est **blacklisté temporairement** pour `WIFI_BLACKLIST_MIN` minutes (défaut 10), la connexion est coupée, et la logique de l'étape 2 reprend — avec repli AP si aucun autre candidat sain n'existe. Le blacklistage temporaire est indispensable pour éviter le **ping-pong** wifi instable ↔ AP toutes les 30 secondes.
- Chaque bascule pour cause d'instabilité est loguée explicitement (`signal trop faible`, `passerelle injoignable`) dans `logs/reseau.log` et visible dans le dashboard via le mode réseau affiché.

Exécuté toutes les ~30 s par un couple `.service`/`.timer` systemd.

### 5.4 Synchronisation Google Drive (rclone)

- Outil : **rclone**, configuré une fois avant le mariage (`rclone config`, remote Google Drive, ex. nommé `gdrive`), dossier de destination ex. `MariageGuestBook`.
- Commande : **`rclone copy`** (jamais `sync`) — n'ajoute que les nouveaux fichiers, **ne supprime jamais rien** ni en local ni sur Drive. Compare taille/date : pas de re-upload.
- Si pas d'internet au moment de l'exécution : échec silencieux, nouvelle tentative au cycle suivant. Log dans `logs/rclone.log`.
- **Paramétrable depuis le dashboard** (page `/rclone`), via un fichier `rclone_config.json` :
  - nom du remote, dossier Drive de destination, intervalle de sync, activation on/off ;
  - bouton **« Synchroniser maintenant »** (lance `rclone copy` en arrière-plan, retourne le résultat) ;
  - indicateur de statut : dernière sync réussie, fichiers en attente, erreurs (lues depuis le log rclone).
- Planification : **timer systemd** (et non cron fixe) dont le fichier `.timer` est régénéré quand l'intervalle change dans le dashboard, suivi de `systemctl daemon-reload && restart`.
- **Point de sécurité identifié** : Flask tourne sous l'utilisateur `pi` qui n'a pas le droit de faire `systemctl` sans mot de passe → prévoir une règle **sudoers ciblée** (NOPASSWD limité aux seules commandes `systemctl daemon-reload` / `systemctl restart <unité rclone>` / `systemctl start <unité rclone>`), jamais un NOPASSWD global.

### 5.5 Transcription (optionnelle, hors événement)

- Le Pi Zero 2 W (512 Mo RAM) **ne permet pas** la transcription temps réel.
- Option retenue : **transcription batch après l'événement** avec `whisper.cpp`, modèle **`tiny` quantisé q5_0**, plus lent que le temps réel — à lancer la nuit sur le Pi, ou transférer les WAV sur un ordinateur plus puissant.
- Ne jamais lancer la transcription pendant l'événement (concurrence CPU/RAM avec l'enregistrement).

### 5.6 Hors périmètre (décisions explicites)

- **Kit mains libres Bluetooth** : jugé faisable plus tard (BlueZ + profils HFP/HSP, purement logiciel, matériel inchangé) mais **explicitement mis de côté** — ne pas l'implémenter.
- Génération de la sonnerie sur la cloche d'origine (tension AC élevée) : abandonnée au profit du haut-parleur externe.

---

## 6. Protection du dashboard par mot de passe (nouvelle exigence)

Exigence : **protection simple par mot de passe** — un seul mot de passe partagé, pas de gestion d'utilisateurs.

**Spécification :**

1. **Mécanisme** : authentification par **session Flask** avec page de login (préférée à HTTP Basic Auth pour l'ergonomie mobile et la compatibilité avec le scan de QR code).
   - Page `/login` : un seul champ mot de passe + bouton. Redirection vers la page demandée après succès.
   - Route `/logout`.
   - Cookie de session signé (Flask `SECRET_KEY` générée aléatoirement au premier démarrage et persistée dans un fichier local, ex. `secret_key.txt`, permissions 600 — pour que les sessions survivent aux redémarrages).
2. **Stockage du mot de passe** : dans un fichier de configuration local (ex. `dashboard_config.json`), stocké **haché** (PBKDF2/scrypt via `werkzeug.security`), jamais en clair dans le code. Prévoir une valeur par défaut documentée à changer à l'installation, ou un petit script CLI `set_password` pour le définir.
3. **Périmètre protégé** : **toutes** les routes HTML **et toutes** les routes `/api/*` (y compris `/api/ring`, `/api/wifi/add`, `/rclone`) — un `before_request` global avec liste blanche limitée à `/login` et aux fichiers statiques strictement nécessaires à la page de login.
   - `/qr` et `/qr/label` sont protégées aussi (elles révèlent les identifiants WiFi de l'AP).
4. **Anti-brute-force léger** : temporisation progressive après N échecs (ex. 1 s de délai après 3 échecs, plafonné), journalisée. Pas besoin de plus sur un réseau local événementiel.
5. **Session** : durée de vie ~12 h (couvre la soirée), « se souvenir de moi » facultatif.
6. **Rappel de contexte** : le serveur reste en HTTP local (pas de TLS) — le mot de passe protège contre la curiosité des invités connectés au même réseau, pas contre un attaquant motivé ; c'est le niveau de sécurité voulu (« simple »).

---

## 7. Surcouches de fiabilité (exigences transverses)

Ces exigences s'appliquent à l'ensemble de l'implémentation. L'appareil doit fonctionner **une journée entière sans intervention**, y compris après coupure de courant.

### 7.1 Démarrage et supervision (systemd)

- **Chaque composant est un service systemd** démarrant au boot :
  - `livre-dor.service` (script principal),
  - `dashboard.service` (Flask),
  - `wifi-or-ap.timer` + `.service` (bascule réseau, toutes les 30 s),
  - `rclone-sync.timer` + `.service` (intervalle dynamique).
- Tous les services longs : `Restart=always`, `RestartSec=5`, avec `StartLimitIntervalSec`/`StartLimitBurst` raisonnables pour éviter les boucles de crash serrées (mais **ne jamais** abandonner définitivement : après le burst, retenter avec un délai plus long).
- `After=network.target` et dépendances explicites ; le script principal ne doit **pas** dépendre du réseau (il doit enregistrer même sans aucun réseau).
- Journalisation systemd (`journalctl`) en complément des logs fichiers.
- **Watchdog applicatif** : le script principal écrit `status.json` avec horodatage à chaque itération ; option `WatchdogSec` systemd (`Type=notify` + `sd_notify`) ou, plus simple, un timer de surveillance qui redémarre `livre-dor.service` si `status.json` n'a pas été mis à jour depuis N minutes.

### 7.2 Robustesse du script principal

- **Gestion d'exceptions globale** : toute exception non prévue → log complet (traceback), écriture de l'état `erreur` dans `status.json`, `GPIO.cleanup()` dans un bloc `finally`, puis le service est relancé par systemd. Jamais de crash silencieux.
- **Sous-processus audio** : timeout sur chaque appel `aplay`/`arecord` ; kill propre (SIGTERM puis SIGKILL) des processus orphelins ; vérifier le code retour et logger les échecs.
- **Carte son absente/débranchée** : au démarrage, vérifier que le périphérique `SOUND_CARD` existe (parsing d'`aplay -l`) ; si absent → état `erreur` visible sur le dashboard + nouvelles tentatives périodiques (l'USB peut mettre du temps à s'énumérer au boot — prévoir une boucle d'attente au démarrage plutôt qu'un échec immédiat).
- **Fichiers audio manquants** : vérifiés au démarrage ; message d'erreur explicite listant les fichiers attendus ; refus de démarrer la boucle sans le message générique et le bip.
- **Debounce matériel/logiciel** : anti-rebond sur le crochet (~50–100 ms) et sur les impulsions du cadran (fenêtres temporelles typiques : impulsion ~60 ms, inter-chiffre > 200 ms) ; ignorer les micro-coupures du crochet pendant l'enregistrement (< 100 ms) pour ne pas tronquer un message sur un faux contact.
- **Écritures atomiques** : `status.json` et tout JSON de config écrits via fichier temporaire + `os.replace()` (jamais de JSON à moitié écrit lisible par le dashboard).
- **Enregistrement** : écrire directement dans le fichier final horodaté ; en cas d'interruption anormale, un WAV tronqué reste lisible (en-tête WAV : préférer un ré-écriture d'en-tête post-enregistrement ou laisser `arecord` gérer). Ne **jamais** écraser un fichier existant (horodatage à la seconde + suffixe incrémental en cas de collision).
- **Enregistrements trop courts** : les fichiers < 2 s (décroché-raccroché immédiat) sont conservés mais marqués/logués, pas supprimés (décision : ne jamais supprimer de données).

### 7.3 Stockage et données

- **Contrôle d'espace disque** : avant chaque enregistrement, vérifier l'espace libre ; sous un seuil (ex. 500 Mo) → état `erreur` sur le dashboard + log, mais continuer d'enregistrer tant que possible ; sous un seuil critique (ex. 100 Mo) → refuser l'enregistrement et jouer le message générique quand même.
- **Rotation des logs** : `logging.handlers.RotatingFileHandler` (ex. 5 × 1 Mo) sur tous les logs applicatifs ; le log rclone est tronqué périodiquement.
- **Aucune suppression automatique** de messages, nulle part (`rclone copy`, jamais `sync` ; pas de purge locale).
- **Sauvegarde à trois niveaux** : microSD locale (source de vérité) → Google Drive (rclone) → possibilité de copie manuelle USB après l'événement.
- **Horloge** : le Pi n'a pas de RTC. Si aucun réseau pendant tout l'événement, l'heure peut être fausse → activer `systemd-timesyncd` + fake-hwclock (restaure la dernière heure connue au boot) ; les horodatages restent monotones et ordonnés même si absolus inexacts.

### 7.4 Réseau et dashboard

- Le fonctionnement du livre d'or est **totalement indépendant du réseau** : aucune fonctionnalité invitée ne doit bloquer sur une absence de wifi/internet.
- Bascule wifi/AP : déjà spécifiée §5.3 (candidats triés par puissance de signal, seuil de signal minimal, surveillance de qualité de la connexion active avec blacklistage temporaire anti-ping-pong, repli AP en dernier recours, AP non coupé prématurément, logs).
- Dashboard : toutes les lectures de fichiers (`status.json`, logs, comptage) tolèrent l'absence du fichier (retour « inconnu » plutôt qu'erreur 500) ; les appels `nmcli`/`rclone` en sous-processus ont un timeout et retournent une erreur JSON propre.
- Échec de création du fichier `ring_trigger` (`OSError`) → réponse HTTP 500 avec message explicite (déjà spécifié).
- Le front-end du dashboard fonctionne **sans aucune ressource internet** (jsQR vendorisé, pas de CDN, CSS inline ou statique local).

### 7.5 Alimentation et environnement

- Alimentation 5 V ≥ 2,5 A de qualité (les brownouts corrompent la microSD).
- Option recommandée : minimiser les écritures (logs avec rotation, pas de swap agressif) ; l'overlayFS lecture seule n'est **pas** retenu (incompatible avec l'enregistrement local), la qualité de l'alimentation et un `fsck` automatique au boot suffisent.
- Prévoir une **procédure d'extinction propre** documentée (bouton dashboard « Éteindre le Pi » est une extension possible — non exigée) ; à défaut, tolérance aux coupures brutales via les points ci-dessus.

### 7.6 Testabilité et vérifications pré-événement

- **Mode `--test`** du script principal : affichage temps réel des 3 GPIO pour valider câblage et sens logiques (`HOOK_ACTIVE_STATE`, `OFFNORMAL_ACTIF_LEVEL`).
- **Checklist de mise en service** (à inclure dans le guide d'installation) :
  1. `aplay -l` / `arecord -l` → renseigner `SOUND_CARD`.
  2. Multimètre sur crochet + contacts du cadran → ajuster les niveaux logiques.
  3. `python3 livre_dor.py --test` → vérifier les 3 broches.
  4. Test audio : tonalité dans l'écouteur seul, sonnerie sur le haut-parleur seul (validation du panning).
  5. Composer chaque chiffre → vérifier le bon message + fallback.
  5bis. Déclencher la sonnerie (dashboard) et décrocher pendant/juste après → vérifier qu'un message aléatoire est joué **sans tonalité ni cadran**, puis bip + enregistrement ; répéter pour vérifier la variation des messages.
  6. Enregistrer un message test → vérifier le WAV et sa synchro Drive.
  7. Débrancher le wifi → vérifier l'apparition de l'AP « Livre-dor-Mariage » et l'accès `192.168.4.1:5000` puis `livredor.local:5000`.
  7bis. Test « wifi zombie » : connecter le Pi à un réseau puis l'éloigner (ou couper la passerelle de ce réseau) → vérifier qu'après ~90 s le Pi bascule seul en AP, que le SSID fautif reste blacklisté ~10 min, et que le dashboard redevient accessible via l'AP.
  8. Couper/remettre l'alimentation → vérifier que tout redémarre seul et que le dashboard demande le mot de passe.

---

## 8. Arborescence et fichiers d'échange

```
/home/pi/livre_dor/
├── livre_dor.py              # script principal (machine à états)
├── prepare_audio.py          # pré-traitement stéréo (usage unique)
├── dashboard_app.py          # serveur Flask
├── wifi_or_ap.sh             # bascule réseau
├── static/
│   └── jsQR.min.js           # décodeur QR vendorisé (servi localement)
├── audio/
│   ├── tonalite.wav          # généré (440+480 Hz), pané gauche
│   ├── bip.wav               # pané gauche
│   ├── ring_out.wav          # sonnerie, panée droite
│   ├── message_generique.wav # pané gauche (fallback)
│   └── message_N.wav         # un par chiffre attribué (N = 0..9), pané gauche
├── messages/                 # enregistrements des invités (WAV horodatés)
├── logs/
│   ├── livre_dor.log         # rotation 5 × 1 Mo
│   ├── reseau.log
│   └── rclone.log
├── status.json               # état courant écrit par livre_dor.py (atomique)
├── ring_trigger              # fichier drapeau (créé par dashboard, consommé par livre_dor.py)
├── rclone_config.json        # paramètres de sync éditables via dashboard
├── dashboard_config.json     # hash du mot de passe dashboard + options
├── secret_key.txt            # clé de session Flask (chmod 600)
└── active_port.txt           # port réellement utilisé par Flask
```

**Interfaces inter-processus (contrats) :**
- `status.json` : `{ "etat": "attente|sonnerie|decroche|numerotation|lecture|appel_repondu|enregistrement|erreur", "derniere_maj": "ISO-8601", "detail": "texte optionnel" }`
- `ring_trigger` : simple existence du fichier = demande de sonnerie ; supprimé par le script principal après exécution.
- `rclone_config.json` : `{ "remote": "gdrive", "dossier": "MariageGuestBook", "intervalle_min": 5, "actif": true }`

---

## 9. Tableau récapitulatif de configuration

| Paramètre | Valeur par défaut | Description / action requise |
|---|---|---|
| `HOOK_PIN` | GPIO 17 (BCM) | Crochet du combiné |
| `DIAL_OFFNORMAL_PIN` | GPIO 27 (BCM) | Contact « cadran en mouvement » |
| `DIAL_PULSE_PIN` | GPIO 22 (BCM) | Contact d'impulsions |
| `HOOK_ACTIVE_STATE` | LOW | État GPIO = décroché — **vérifier au multimètre** |
| `OFFNORMAL_ACTIF_LEVEL` | à vérifier | Sens logique du contact off-normal |
| `SOUND_CARD` / `CARTE_SON` | `plughw:1,0` | À confirmer via `aplay -l` / `arecord -l` |
| `RING_INTERVAL_SEC` | 90 | Intervalle sonnerie en attente |
| `RING_ANSWER_GRACE_SEC` | 5 | Fenêtre après la fin de la sonnerie pendant laquelle un décroché est traité comme un « appel entrant » (message aléatoire, sans cadran) |
| `MAX_RECORD_SEC` | 120 | Durée max d'un message invité |
| `WEB_PORT` | 5000 | Port dashboard, **fixe** |
| `WEB_PORT_MAX_ATTEMPTS` | 1 | Pas de repli de port |
| `USE_MDNS` / `MDNS_HOSTNAME` | True / `livredor` | URL stable `http://livredor.local:5000/` |
| Hostname du Pi | `livredor` | `hostnamectl set-hostname livredor` + restart avahi |
| `AP_CONNECTION_NAME` | `GuestbookAP` | Nom du profil nmcli de l'AP |
| SSID de l'AP | `Livre-dor-Mariage` | Réseau de secours |
| IP en mode AP | `192.168.4.1` | Fixe |
| Intervalle timer réseau | 30 s | `wifi_or_ap` |
| `CONNECT_TIMEOUT` | 15 s | Par tentative de connexion wifi |
| `WIFI_SIGNAL_MIN` | 25 % | Signal minimal : en dessous, un réseau n'est ni tenté ni conservé |
| `WIFI_FAIL_THRESHOLD` | 3 | Contrôles qualité échoués consécutifs (signal ou ping passerelle) avant abandon du wifi actif |
| `WIFI_BLACKLIST_MIN` | 10 min | Durée d'exclusion temporaire d'un SSID jugé instable (anti-ping-pong wifi ↔ AP) |
| Remote rclone | `gdrive` | Configuré une fois via `rclone config` |
| Dossier Drive | `MariageGuestBook` | Modifiable via dashboard |
| Intervalle sync | 5 min | Modifiable via dashboard |
| Mot de passe dashboard | à définir à l'installation | Stocké haché ; protège toutes les routes |
| Seuils disque | 500 Mo / 100 Mo | Alerte / refus d'enregistrement |
| Taille étiquette QR | 45 mm | `?taille=NN` sur `/qr/label` |

---

## 10. Dépendances logicielles

- OS : Raspberry Pi OS Lite (Bookworm), NetworkManager (`nmcli`), `avahi-daemon` (mDNS), systemd.
- Paquets : `alsa-utils` (aplay/arecord), `rclone`, `python3-pip`.
- Python : `RPi.GPIO` (ou `gpiozero`/`lgpio` selon préférence de l'implémenteur), `Flask`, `qrcode[pil]`, `pydub` (+ `ffmpeg` pour pydub), `werkzeug` (hash mot de passe, inclus avec Flask).
- Front vendorisé : `jsQR.min.js` (npm, servi en statique).
- Optionnel post-événement : `whisper.cpp` + modèle `tiny` q5_0.
