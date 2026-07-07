# Plan de développement — Livre d'or téléphonique (Socotel S63)

**Basé sur :** `specification_livre_dor_telephonique.md` (v1.0 — 6 juillet 2026)
**Objectif de ce document :** découper l'implémentation en sprints livrables, testables indépendamment, dans un ordre qui limite les dépendances bloquantes (le matériel et l'audio d'abord, la fiabilité et le réseau ensuite, le confort en dernier).

---

## Vue d'ensemble des sprints

| Sprint | Thème | Dépend de |
|---|---|---|
| 0 | Mise en place environnement & squelette projet | — |
| 1 | Pipeline audio (pré-traitement + lecture/enregistrement ALSA) | 0 |
| 2 | Machine à états — GPIO & scénario nominal (appel sortant) | 0, 1 |
| 3 | Scénario « appel entrant » & sonnerie périodique | 2 |
| 4 | Surcouches de fiabilité du script principal | 2, 3 |
| 5 | Dashboard Flask — lecture seule (statut, logs, messages) | 1 (statut minimal dès 2) |
| 6 | Authentification dashboard (mot de passe) | 5 |
| 7 | QR codes & provisioning WiFi via le dashboard | 5, 6 |
| 8 | Bascule réseau WiFi/AP + surveillance qualité | 4 |
| 9 | Synchronisation Google Drive (rclone) + config dashboard | 4, 5, 6 |
| 10 | Services systemd, supervision, watchdog global | 2–9 |
| 11 | Transcription batch (optionnelle) | 4 |
| 12 | Recette finale, checklist de mise en service, documentation | tous |

Chaque sprint liste : objectifs, tâches, fichiers concernés, critères d'acceptation (dérivés des exigences de la spec), et risques.

---

## Sprint 0 — Mise en place environnement & squelette projet

**Objectif :** disposer d'un Raspberry Pi OS Lite fonctionnel, de l'arborescence du projet et des dépendances installées, avant d'écrire la moindre logique métier.

**Tâches :**
- Flasher Raspberry Pi OS Lite (Bookworm) sur la microSD, activer SSH.
- `hostnamectl set-hostname livredor` + vérifier `avahi-daemon` actif (mDNS).
- Installer les paquets système : `alsa-utils`, `rclone`, `python3-pip`, `ffmpeg` (requis par `pydub`), NetworkManager déjà présent sous Bookworm.
- Installer les paquets Python : `RPi.GPIO` (ou `gpiozero`/`lgpio`), `Flask`, `qrcode[pil]`, `pydub`, `werkzeug`.
- Créer l'arborescence complète décrite en §8 de la spec (`/home/pi/livre_dor/` avec sous-dossiers `audio/`, `messages/`, `logs/`, `static/`).
- Initialiser le dépôt git, `.gitignore` (exclure `messages/`, `logs/`, `secret_key.txt`, `dashboard_config.json` réels, `status.json`, `rclone_config.json` réel).
- Brancher la carte son USB, lister les périphériques (`aplay -l`, `arecord -l`) pour fixer `SOUND_CARD` provisoire.

**Livrables :** dépôt initialisé, arborescence en place, dépendances installées et vérifiées (`pip freeze`, `dpkg -l`).

**Critères d'acceptation :**
- Le Pi est accessible en SSH via `livredor.local`.
- `aplay -l` détecte la carte son USB.
- L'arborescence correspond exactement à §8.

**Risques :** énumération lente de la carte son USB au boot (à garder en tête pour le Sprint 4, §7.2).

---

## Sprint 1 — Pipeline audio (pré-traitement + primitives lecture/enregistrement)

**Objectif :** obtenir tous les fichiers audio finaux (pané un seul canal) et des fonctions robustes de lecture/enregistrement réutilisables par la machine à états.

**Tâches :**
- Écrire `prepare_audio.py` (§4.2) : conversion de chaque fichier source en stéréo pané 100 % sur un canal (gauche pour écouteur, droit pour sonnerie), gain configurable par fichier via `pydub`.
- Générer `audio/tonalite.wav` par synthèse (mélange sinus 440 Hz + 480 Hz), pané gauche, gain −12 dB.
- Traiter `audio/bip.wav` (pané gauche, −6 dB), `audio/ring_out.wav` (pané droit, 0 dB), `audio/message_generique.wav` et `audio/message_N.wav` (pané gauche, −6 à −12 dB).
- Écrire les fonctions bas niveau de lecture (`aplay` en sous-processus) et d'enregistrement (`arecord` en sous-processus) :
  - lecture interruptible (poll toutes les ~100 ms, kill du sous-processus sur demande) ;
  - enregistrement borné dans le temps, écriture directe vers le fichier final horodaté ;
  - timeout sur chaque appel, gestion SIGTERM puis SIGKILL, vérification du code retour.
- Script/CLI de vérification manuelle : jouer chaque fichier, confirmer au casque/haut-parleur que le panning est correct.

**Fichiers concernés :** `prepare_audio.py`, module interne de lecture/enregistrement (ex. `audio_io.py`) réutilisé par `livre_dor.py`.

**Critères d'acceptation :**
- Chaque fichier généré est bien mono-canal actif (vérifiable avec un lecteur audio ou `ffprobe`), l'autre canal silencieux.
- La tonalité et le bip ne sortent que sur l'écouteur ; la sonnerie ne sort que sur le haut-parleur externe (test §7.6 point 4).
- Une lecture lancée puis tuée à mi-parcours s'arrête réellement (pas de zombie `aplay`).
- Un enregistrement coupé brutalement laisse un WAV lisible (même tronqué).

**Risques :** dépendance `ffmpeg` manquante fait échouer `pydub` silencieusement — à vérifier explicitement à l'installation.

---

## Sprint 2 — Machine à états : GPIO & scénario nominal (appel sortant)

**Objectif :** implémenter le cœur de `livre_dor.py` pour le parcours invité nominal (hors sonnerie), sans encore les surcouches de fiabilité ni le scénario entrant.

**Tâches :**
- Lecture des 3 GPIO (`HOOK_PIN`=17, `DIAL_OFFNORMAL_PIN`=27, `DIAL_PULSE_PIN`=22), pull-up interne, paramètres `HOOK_ACTIVE_STATE` / `OFFNORMAL_ACTIF_LEVEL` configurables.
- Anti-rebond logiciel sur crochet (~50–100 ms) et impulsions du cadran (impulsion ~60 ms, inter-chiffre > 200 ms).
- Comptage des impulsions pendant que le contact off-normal est actif ; validation du chiffre au retour au repos (0 = 10 impulsions).
- Implémenter les états : `attente` → `decroche` (tonalité) → `numerotation` → `lecture_message` → `enregistrement` → retour `attente`.
- Mapping chiffre → fichier `message_N.wav`, fallback `message_generique.wav` si non attribué.
- Toute lecture interrompue immédiatement au raccroché (surveillance crochet pendant `aplay`).
- Enregistrement arrêté au raccroché ou à `MAX_RECORD_SEC` (défaut 120 s), nommage horodaté `message_YYYY-MM-DD_HH-MM-SS.wav`, suffixe incrémental en cas de collision (jamais d'écrasement).
- **Mode `--test`** : affichage temps réel de l'état des 3 GPIO pour validation câblage.

**Fichiers concernés :** `livre_dor.py` (première version, sans sonnerie/entrant/fiabilité avancée).

**Critères d'acceptation (checklist §7.6, points 1–5) :**
- `python3 livre_dor.py --test` reflète correctement décroché/raccroché et rotation du cadran.
- Décrocher hors sonnerie joue la tonalité, s'arrête à la première impulsion.
- Composer chaque chiffre joue le bon message ; un chiffre non mappé joue le message générique.
- Raccrocher pendant tonalité/message/bip interrompt immédiatement la lecture.
- Un message enregistré est sauvegardé en WAV horodaté au raccroché.

**Risques :** sens logique des contacts (crochet, off-normal) à vérifier au multimètre avant de coder les constantes — bloquant si inversé.

---

## Sprint 3 — Scénario « appel entrant » & sonnerie périodique

**Objectif :** ajouter la branche parallèle « appel entrant » (simulation d'un vrai appel) et la sonnerie (périodique + déclenchement distant).

**Tâches :**
- Sonnerie périodique en état `attente` : joue `ring_out.wav` toutes les `RING_INTERVAL_SEC` (défaut 90 s), interrompue immédiatement au décroché.
- Vérification à chaque itération de l'existence du fichier drapeau `ring_trigger` → sonnerie immédiate puis suppression du fichier.
- Drapeau interne « sonnerie récente » actif pendant la sonnerie + `RING_ANSWER_GRACE_SEC` (défaut 5 s) après sa fin.
- Si décroché pendant ce drapeau → transition vers `appel_repondu` : coupe la sonnerie, **pas de tonalité, cadran ignoré**, tirage aléatoire d'un message parmi tous les `message_N.wav` + `message_generique.wav`, sans répéter le dernier tiré, puis bip → enregistrement → sauvegarde au raccroché.
- Si décroché hors fenêtre → flux nominal (Sprint 2) inchangé.
- Impulsions de cadran reçues en `appel_repondu` : ignorées et loguées en debug, sans effet.
- Si raccroché pendant message/bip en `appel_repondu` : aucun enregistrement créé, retour `attente` (comme le flux nominal).

**Fichiers concernés :** `livre_dor.py` (extension de la machine à états, ajout des états `sonnerie` et `appel_repondu`).

**Critères d'acceptation (checklist §7.6, point 5bis) :**
- Décrocher pendant la sonnerie → message aléatoire sans tonalité ni cadran, puis bip + enregistrement.
- Décrocher dans les 5 s après la fin de la sonnerie → même comportement.
- Décrocher après la fenêtre de grâce → comportement nominal (tonalité).
- Répéter plusieurs fois : jamais deux fois de suite le même message tiré.
- Déclencher `ring_trigger` (fichier créé manuellement pour le test) → sonnerie immédiate, fichier supprimé après.

**Risques :** race condition entre la boucle principale et l'écriture/suppression de `ring_trigger` par le dashboard — traiter la lecture du fichier de façon atomique (tester existence puis supprimer, tolérer un `FileNotFoundError` si déjà consommé).

---

## Sprint 4 — Surcouches de fiabilité du script principal

**Objectif :** rendre `livre_dor.py` robuste à une exécution de plusieurs heures sans intervention (§7.1, 7.2, 7.3 côté script principal).

**Tâches :**
- Gestion d'exception globale au niveau de la boucle principale : toute exception non prévue → log complet (traceback), état `erreur` écrit dans `status.json`, `GPIO.cleanup()` en `finally`, puis laisser systemd relancer le service (pas de retry interne infini ici).
- Vérification au démarrage : périphérique `SOUND_CARD` présent (parsing `aplay -l`), avec boucle d'attente (l'USB peut mettre du temps à s'énumérer) avant de basculer en `erreur`.
- Vérification au démarrage : présence des fichiers audio requis (au minimum `message_generique.wav` et `bip.wav`) ; refus de démarrer la boucle sinon, message d'erreur explicite listant les fichiers manquants.
- Écriture continue et **atomique** de `status.json` (fichier temporaire + `os.replace()`) à chaque itération, avec le contrat `{ "etat", "derniere_maj", "detail" }` (§8).
- Contrôle d'espace disque avant chaque enregistrement : seuil d'alerte 500 Mo (état `erreur` affiché mais enregistrement autorisé), seuil critique 100 Mo (enregistrement refusé, message générique tout de même joué).
- Ignorer les micro-coupures du crochet (< 100 ms) pendant l'enregistrement pour ne pas tronquer un message sur un faux contact.
- Conserver (sans supprimer) les enregistrements < 2 s, en les marquant/loguant.
- Rotation des logs applicatifs (`RotatingFileHandler`, 5 × 1 Mo) sur `logs/livre_dor.log`.
- Horloge : activer `systemd-timesyncd` + `fake-hwclock` pour restaurer la dernière heure connue au boot (horodatages monotones même sans réseau).

**Fichiers concernés :** `livre_dor.py` (durcissement), configuration système (`fake-hwclock`, `timesyncd`).

**Critères d'acceptation :**
- Débrancher la carte son puis démarrer le script → état `erreur` visible, tentatives périodiques, pas de crash.
- Supprimer un fichier audio requis → le script refuse de démarrer avec un message clair.
- Simuler un disque presque plein → comportement conforme aux deux seuils.
- Tuer le processus en pleine écriture de `status.json` → jamais de JSON à moitié écrit lu par un tiers.
- Couper l'alimentation brutalement pendant un enregistrement → le WAV existant reste lisible au redémarrage.

**Risques :** le calcul d'espace disque et la vérification carte son ajoutent de la latence au démarrage — à garder asynchrone/non bloquant pour la boucle GPIO.

---

## Sprint 5 — Dashboard Flask (lecture seule)

**Objectif :** superviser l'appareil pendant l'événement via une page web simple, sans encore authentification ni actions d'écriture sensibles autres que la sonnerie.

**Tâches :**
- Serveur Flask sur `0.0.0.0:5000` (`WEB_PORT_MAX_ATTEMPTS = 1` : pas de repli de port, arrêt avec message clair si le port est pris, port réel écrit dans `active_port.txt`).
- Page `/` : état en direct (code couleur), nombre de messages, mode réseau + IP, dernières lignes de log ; auto-rafraîchissement (statut ~4 s, logs ~8 s) avec préservation du scroll côté JS.
- `/api/status` : JSON du contenu de `status.json`, tolérant l'absence de fichier (retour « inconnu », jamais de 500).
- `/api/logs` : JSON des ~150 dernières lignes de `logs/livre_dor.log`, tolérant l'absence de fichier.
- `/api/messages/count` : JSON du nombre de fichiers dans `messages/`.
- `/api/ring` (POST) : crée `ring_trigger` ; en cas d'`OSError` → HTTP 500 avec message explicite.
- Toutes les lectures de fichiers côté dashboard tolèrent l'absence de fichier (§7.4).

**Fichiers concernés :** `dashboard_app.py`, `static/` (CSS/JS minimal, sans dépendance CDN).

**Critères d'acceptation :**
- Le dashboard reste utilisable même si `status.json` ou les logs n'existent pas encore.
- `/api/ring` crée bien `ring_trigger` et déclenche la sonnerie côté `livre_dor.py` (Sprint 3).
- Si le port 5000 est occupé, le service s'arrête avec un log clair (pas de bascule silencieuse).

**Risques :** aucune pour ce sprint tant que l'accès n'est pas encore protégé — **ne pas exposer ce dashboard sur un réseau non maîtrisé avant le Sprint 6.**

---

## Sprint 6 — Authentification du dashboard

**Objectif :** protéger l'ensemble du dashboard par un mot de passe unique (§6), avant toute mise en situation réelle avec des invités sur le même réseau.

**Tâches :**
- Génération et persistance de `SECRET_KEY` Flask au premier démarrage dans `secret_key.txt` (permissions 600).
- Stockage du mot de passe haché (PBKDF2/scrypt via `werkzeug.security`) dans `dashboard_config.json` ; script CLI `set_password` pour le définir/changer.
- Page `/login` (champ mot de passe unique + bouton), redirection vers la page initialement demandée après succès ; route `/logout`.
- `before_request` global protégeant **toutes** les routes HTML et `/api/*` (y compris `/api/ring`), liste blanche limitée à `/login` et aux ressources statiques strictement nécessaires à cette page.
- Anti-brute-force léger : temporisation progressive après N échecs (ex. 1 s après 3 échecs, plafonnée), journalisée.
- Durée de session ~12 h, case « se souvenir de moi » facultative.

**Fichiers concernés :** `dashboard_app.py` (middleware auth, routes `/login`/`/logout`), `set_password` (script CLI), `dashboard_config.json`, `secret_key.txt`.

**Critères d'acceptation (checklist §7.6, point 8) :**
- Toute route protégée redirige vers `/login` sans session valide.
- Après redémarrage du service, une session déjà ouverte reste valide (clé persistée).
- Un mot de passe incorrect répété déclenche la temporisation, journalisée dans les logs.

**Risques :** oublier une route dans la liste blanche/protection — vérifier explicitement `/qr` et `/qr/label` (Sprint 7), qui révèlent les identifiants WiFi de l'AP.

---

## Sprint 7 — QR codes & provisioning WiFi

**Objectif :** faciliter la connexion des invités/organisateurs au dashboard et la connexion du Pi à un WiFi de la salle, sans dépendance internet côté client.

**Tâches :**
- `/qr` (protégée) : génère avec `qrcode[pil]` (a) un QR WiFi avec les identifiants de l'AP (affiché seulement en mode AP), (b) un QR de l'URL stable `http://livredor.local:5000/`.
- `/qr/label` (protégée) : étiquette imprimable dimensionnée en mm (défaut 45 mm, paramètre `?taille=NN`).
- `/wifi` (protégée) : page pour photographier un QR WiFi de la salle, décodage 100 % côté client avec `jsQR.min.js` vendorisé (`static/jsQR.min.js`, aucune requête internet), confirmation du SSID détecté par l'utilisateur avant envoi.
- `/api/wifi/add` (POST, protégée) : crée le profil WiFi via `nmcli` et tente la connexion ; avertissement explicite dans l'UI sur la perte de connexion si le Pi est en mode AP au moment de la confirmation.
- Adressage stable : `USE_MDNS=True`, `MDNS_HOSTNAME="livredor"` pour que tous les QR encodent l'URL mDNS, jamais une IP brute.

**Fichiers concernés :** `dashboard_app.py` (routes `/qr`, `/qr/label`, `/wifi`, `/api/wifi/add`), `static/jsQR.min.js` (vendorisé via npm).

**Critères d'acceptation :**
- Scanner le QR de `/qr` avec un téléphone ouvre bien `http://livredor.local:5000/`.
- `/wifi` fonctionne sans connexion internet sur l'appareil qui scanne (vérifier en coupant les données mobiles).
- `/api/wifi/add` connecte effectivement le Pi au réseau visé (test en environnement réel).

**Risques :** disponibilité de `jsQR.min.js` doit être vérifiée en amont (vendoring), ne pas dépendre d'un CDN.

---

## Sprint 8 — Bascule réseau WiFi/AP & surveillance qualité

**Objectif :** garantir que le dashboard reste joignable quelle que soit la disponibilité WiFi du lieu de réception (§5.3, §7.4).

**Tâches :**
- `wifi_or_ap.sh` : si déjà connecté à un vrai WiFi (≠ profil AP) et connexion saine → ne rien faire.
- Sinon : rescan, lister les profils connus dont le SSID est visible, trier par puissance de signal décroissante, essayer dans l'ordre (`CONNECT_TIMEOUT`=15 s chacun), en écartant les réseaux sous `WIFI_SIGNAL_MIN` (25 %) et les SSID temporairement blacklistés.
- Repli sur le point d'accès local (`GuestbookAP`, SSID `Livre-dor-Mariage`, IP fixe `192.168.4.1`) si aucun candidat ne fonctionne ; l'AP n'est coupé qu'au moment où une vraie tentative de connexion commence.
- Surveillance de qualité de la connexion active à chaque exécution du timer (30 s) : contrôle de signal (`WIFI_SIGNAL_MIN`) + ping de la passerelle ; compteur d'échecs consécutifs persisté (ex. `/run/livre_dor/wifi_health`), remis à zéro sur succès.
- Après `WIFI_FAIL_THRESHOLD` (3) échecs consécutifs : blacklist temporaire du SSID pour `WIFI_BLACKLIST_MIN` (10 min), coupure de la connexion, relance de la logique de sélection (repli AP si besoin).
- Journalisation de chaque décision (connexion, bascule AP, blacklist, cause) dans `logs/reseau.log`.
- Couple `.service`/`.timer` systemd exécutant le script toutes les ~30 s.

**Fichiers concernés :** `wifi_or_ap.sh`, unités systemd `wifi-or-ap.service`/`.timer`.

**Critères d'acceptation (checklist §7.6, points 7 et 7bis) :**
- Débrancher le WiFi → apparition de l'AP « Livre-dor-Mariage », accès `192.168.4.1:5000` puis `livredor.local:5000`.
- Connecter le Pi à un réseau puis dégrader son signal/couper sa passerelle → bascule automatique en AP après ~90 s, SSID blacklisté ~10 min, dashboard de nouveau accessible via l'AP.
- Pas de ping-pong WiFi ↔ AP toutes les 30 s après une bascule.

**Risques :** ce sprint touche à la connectivité SSH du Pi lui-même — tester en priorité avec un accès physique/console de secours (risque de se couper l'accès pendant le développement).

---

## Sprint 9 — Synchronisation Google Drive (rclone) + configuration dashboard

**Objectif :** sauvegarder automatiquement les messages vers Google Drive sans jamais risquer de perte de données locale (§5.4).

**Tâches :**
- Configuration initiale unique de `rclone config` (remote `gdrive`), dossier cible `MariageGuestBook`.
- `rclone copy` (jamais `sync`) planifié via timer systemd (`rclone-sync.timer`/`.service`), régénéré quand l'intervalle change (`systemctl daemon-reload && restart`).
- Échec silencieux si pas d'internet, nouvelle tentative au cycle suivant, log dans `logs/rclone.log` (tronqué périodiquement).
- Page `/rclone` (protégée) : édition de `rclone_config.json` (remote, dossier, intervalle, actif on/off), bouton « Synchroniser maintenant » (lance `rclone copy` en arrière-plan et retourne le résultat), indicateur de statut (dernière sync réussie, fichiers en attente, erreurs).
- Règle **sudoers ciblée** NOPASSWD limitée à `systemctl daemon-reload`, `systemctl restart <unité rclone>`, `systemctl start <unité rclone>` pour l'utilisateur `pi` — jamais de NOPASSWD global.

**Fichiers concernés :** `dashboard_app.py` (route `/rclone`), `rclone_config.json`, `logs/rclone.log`, règle sudoers dédiée, unités `rclone-sync.service`/`.timer`.

**Critères d'acceptation (checklist §7.6, point 6) :**
- Un message test enregistré est retrouvé sur le Drive après le prochain cycle de sync.
- Couper l'internet pendant un cycle → pas de crash, retry au cycle suivant, erreur visible dans `/rclone`.
- Changer l'intervalle depuis le dashboard régénère bien le timer sans intervention manuelle en shell.
- Aucun fichier local ou distant n'est jamais supprimé par le processus de sync.

**Risques :** la règle sudoers est le point sensible identifié dans la spec — la restreindre strictement aux 3 commandes citées, à auditer avant l'événement.

---

## Sprint 10 — Services systemd, supervision, watchdog global

**Objectif :** faire tenir l'ensemble du système une journée entière sans intervention, y compris après coupure de courant (§7.1).

**Tâches :**
- Unités systemd pour chaque composant : `livre-dor.service`, `dashboard.service`, `wifi-or-ap.timer`/`.service` (déjà créés Sprint 8), `rclone-sync.timer`/`.service` (déjà créés Sprint 9).
- `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec`/`StartLimitBurst` réglés pour absorber les crashs en rafale sans jamais abandonner définitivement (retenter avec délai plus long après le burst).
- `After=network.target` + dépendances explicites, mais `livre-dor.service` ne doit pas exiger le réseau pour démarrer/fonctionner.
- Watchdog applicatif : timer de surveillance qui redémarre `livre-dor.service` si `status.json` n'a pas été mis à jour depuis N minutes (ou `WatchdogSec` + `sd_notify` si `Type=notify` est retenu).
- Vérifier la cohérence de tous les logs via `journalctl` en complément des fichiers.

**Fichiers concernés :** fichiers unit systemd (`*.service`, `*.timer`), éventuel script de watchdog dédié.

**Critères d'acceptation (checklist §7.6, points 3 et 8) :**
- Redémarrage à froid du Pi → tous les services démarrent seuls, dashboard demande le mot de passe.
- Tuer brutalement `livre_dor.py` → systemd le relance en quelques secondes.
- Geler artificiellement `livre_dor.py` (`status.json` non mis à jour) → le watchdog le redémarre après le délai configuré.

**Risques :** un `RestartSec` trop court combiné à un bug systématique peut saturer le CPU d'un Pi Zero — surveiller les `StartLimitBurst`.

---

## Sprint 11 — Transcription batch (optionnelle, hors événement)

**Objectif :** permettre une transcription texte des messages après le mariage, sans jamais interférer avec l'enregistrement pendant l'événement (§5.5).

**Tâches :**
- Installer `whisper.cpp` + modèle `tiny` quantisé `q5_0`.
- Script batch parcourant `messages/*.wav`, lançant la transcription (nuit sur le Pi ou transfert vers une machine plus puissante), sortie texte à côté de chaque WAV (ex. `.txt` associé) sans jamais modifier/supprimer l'audio source.
- Documentation explicite : ne jamais lancer ce script pendant l'événement (concurrence CPU/RAM avec l'enregistrement).

**Fichiers concernés :** script batch dédié (ex. `transcribe_batch.py`), hors des services temps réel.

**Critères d'acceptation :**
- Le script tourne uniquement en exécution manuelle/planifiée post-événement, jamais comme service auto-démarré.
- Aucun impact sur les fichiers `messages/*.wav` existants (lecture seule).

**Risques :** faible priorité — sprint explicitement optionnel, à ne traiter qu'une fois tout le reste stable.

---

## Sprint 12 — Recette finale & documentation de mise en service

**Objectif :** valider l'ensemble du système avec la checklist de mise en service (§7.6) et livrer un guide d'installation reproductible.

**Tâches :**
- Dérouler intégralement la checklist §7.6 (points 1 à 8) sur le matériel final assemblé.
- Rédiger le guide d'installation : câblage (§3), configuration (§9), dépendances (§10), procédure de premier démarrage, réglage du potentiomètre PAM8403.
- Vérifier tous les paramètres du tableau récapitulatif (§9) sont bien configurés pour le matériel réel (`HOOK_ACTIVE_STATE`, `OFFNORMAL_ACTIF_LEVEL`, `SOUND_CARD`, etc.).
- Test de charge : simuler plusieurs cycles décroché/composition/enregistrement/raccroché à la suite pour vérifier l'absence de fuite de sous-processus ou de fichiers.
- Test de coupure d'alimentation en pleine soirée simulée (redémarrage à froid, vérification dashboard + mot de passe + services).

**Livrables :** checklist signée, guide d'installation, système jugé prêt pour l'événement.

**Critères d'acceptation :** tous les points de la checklist §7.6 passent sans intervention manuelle autre que celle prévue par la procédure.

---

## Notes transverses valables sur tous les sprints

- **Aucune suppression automatique** de données à aucune étape (messages, logs de sync) — respecter cette règle même dans le code de test.
- Toute nouvelle route Flask doit être ajoutée à la liste protégée du Sprint 6 dès sa création (ne pas attendre une passe de sécurité a posteriori).
- Tout appel à un sous-processus externe (`aplay`, `arecord`, `nmcli`, `rclone`) doit avoir un timeout et un traitement d'erreur explicite dès son introduction, pas différé à un sprint « fiabilité ».
- Les paramètres marqués « à vérifier au multimètre » dans la spec (§9) doivent être confirmés physiquement avant tout câblage définitif — ne pas les coder en dur avant cette vérification.
