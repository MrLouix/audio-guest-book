# Checklist de mise en service — Livre d'or téléphonique

Basée sur la checklist du §7.6 de la [spécification](specification_livre_dor_telephonique.md). À dérouler intégralement sur le **matériel final assemblé**, avant l'événement. Cocher chaque case au fur et à mesure ; noter la date et qui l'a réalisée en bas de page.

La plupart des points nécessitent le Raspberry Pi, le téléphone câblé et un smartphone — ils ne peuvent pas être vérifiés autrement que physiquement. Les points marqués **(auto)** disposent d'un outil du dépôt qui automatise une partie de la vérification.

---

## 1. Identification et configuration de la carte son

- [ ] `aplay -l` et `arecord -l` exécutés sur le Pi ; **IQaudio Codec Zero** repéré (ex. `card 1`).
- [ ] `config.SOUND_CARD` (ou variable d'environnement `SOUND_CARD`) mis à jour en conséquence (ex. `plughw:1,0`).
- [ ] `sudo ./scripts/audio-setup.sh headphone` exécuté une fois : « Etat sauvegarde (asound.state) » affiché — la carte sera correcte dès le prochain boot.
- [ ] `./scripts/audio-setup.sh status` : **ALC off** (`off,off`), au moins une sortie active, aucune ligne `! echec numid=`. Un numid en échec signale un glissement de driver : ne pas aller plus loin sans l'avoir corrigé.

## 2. Sens logique des contacts (multimètre)

- [ ] Crochet du combiné testé au multimètre (normalement ouvert ou fermé au repos) ; `HOOK_ACTIVE_STATE` ajusté si besoin.
- [ ] Contact « off-normal » du cadran testé ; `OFFNORMAL_ACTIF_LEVEL` ajusté si besoin.
- [ ] Contact d'impulsions testé **séparément** : il n'a pas forcément le même sens logique que l'off-normal ; `PULSE_ACTIF_LEVEL` ajusté si besoin.
- [ ] `python3 tests/scope_impulsions.py --niveaux`, cadran immobile : la broche d'impulsions est au niveau **opposé** à `PULSE_ACTIF_LEVEL`. Si elle est déjà sur le niveau actif, le contact est court-circuité ou la paire de fils est la mauvaise — inutile d'aller plus loin.
- [ ] `python3 tests/scope_impulsions.py --reel --numero 6` (composer un 6) : le balayage affiche un **palier** de valeurs justes. `PULSE_MIN_REPOS_SEC` réglé au centre de ce palier, et non sur une valeur isolée qui tombe juste par hasard.

## 2bis. Journaux en mode mise en service

À faire **avant** tout le reste : sans ce réglage, le détail du cadran et des
sorties audio reste invisible, et chaque anomalie des étapes suivantes se
diagnostique à l'aveugle.

- [ ] `LOG_LEVEL` passé à `DEBUG` (page `/settings` du dashboard), puis `sudo systemctl restart livre-dor`. Pour un lancement à la main, `python3 src/livre_dor.py --verbeux` suffit et ne touche pas à la configuration.
- [ ] Au démarrage, `tail -f logs/livre_dor.log` montre le récapitulatif : `Journalisation au niveau DEBUG`, puis la ligne `Audio : carte ..., sonnerie sur ..., combiné sur ...` et la ligne `GPIO échantillonnés à ...`. **Vérifier que la carte et les sorties annoncées sont bien celles câblées.**
- [ ] Une rotation du cadran produit le détail attendu : `impulsions -> actif (état précédent tenu N ms)`, `impulsion comptée (k ...)`, `chiffre validé : N`. Les durées affichées servent à régler `PULSE_MIN_ACTIF_SEC` et `PULSE_MIN_REPOS_SEC` sans arrêter le service.
- [ ] Aucune ligne `impulsion ignorée : le cadran est au repos` alors que personne ne touche au téléphone. Si elle apparaît, le contact d'impulsions grésille au repos : reprendre l'étape 2 avant d'aller plus loin.
- [ ] **À la fin de la mise en service**, `LOG_LEVEL` repassé à `INFO` et service redémarré.

## 3. Mode `--test` **(auto : outil fourni)**

- [ ] `python3 src/livre_dor.py --test` lancé ; décrocher → l'affichage bascule bien sur « décroché ».
- [ ] Tourner le cadran → l'affichage détecte bien le mouvement et les impulsions.
- [ ] Raccrocher → retour à l'état de repos affiché.

## 3bis. Tests unitaires par fonction **(auto : outils fournis)**

Un script par fonction, à lancer sur le Pi câblé ; chacun affiche un rapport
clair et sort en erreur si une vérification échoue (détail : [`tests/README.md`](../tests/README.md)).

```bash
python3 tests/run_tous.py --reel     # ou un script à la fois, ci-dessous
```

- [ ] `python3 tests/test_decroche.py --reel` : décroché détecté en moins d'une seconde, sens logique correct.
- [ ] `python3 tests/test_raccroche.py --reel` : raccroché détecté, aucun faux contact pendant 5 s combiné posé.
- [ ] `python3 tests/test_composition.py --reel` : les chiffres demandés (3 puis 0) sont décodés exactement.
- [ ] `python3 tests/test_lecture.py --reel` : tonalité, message et bip entendus dans l'écouteur ; le raccroché coupe la lecture.
- [ ] `python3 tests/test_sonnerie.py --reel` : sonnerie sur le haut-parleur externe, coupée au décroché.
- [ ] `python3 tests/test_enregistrement.py --reel` : message enregistré en raccrochant, puis relu correctement.
- [ ] `python3 tests/test_status.py --reel` : `status.json` frais, loin du seuil de redémarrage du watchdog.

## 4. Test audio (deux sorties du codec)

- [ ] Format des fichiers générés contrôlé — **48 000 Hz, 2 canaux, 16 bits** :
      `soxi audio/*.wav` (ou `ffprobe`). Des fichiers à 44 100 Hz ou mono sont
      des restes de l'ancien câblage : relancer `python3 src/prepare_audio.py`.
- [ ] `./scripts/audio-setup.sh lineout && aplay -D plughw:1,0 audio/ring_out.wav`
      → sonnerie entendue **uniquement** sur le haut-parleur de sonnerie.
- [ ] `./scripts/audio-setup.sh headphone && aplay -D plughw:1,0 audio/message_generique.wav`
      → message entendu dans **les deux** écouteurs (combiné et secondaire), **au même niveau**.
      Un écouteur muet = câblage du jack casque à revoir (les fichiers portent le même signal sur les deux pistes).
- [ ] Aucun « pop » gênant au moment de la bascule entre les deux sorties.
- [ ] `python3 src/prepare_audio.py --play-all` : chaque fichier sort sur la bonne destination.
- [ ] Volume du haut-parleur réglé au potentiomètre PAM8403 à un niveau approprié (ni inaudible, ni agressif).

## 4bis. Capture (prérequis RNNoise et full duplex)

- [ ] Enregistrer 5 s (`python3 src/audio_io.py record /tmp/essai.wav --duration 5`) en parlant dans le combiné.
- [ ] `soxi /tmp/essai.wav` → **48 000 Hz, 2 canaux, 16 bits**.
- [ ] **Les deux pistes portent du signal** (`ffmpeg -i /tmp/essai.wav -af astats -f null -` :
      comparer les niveaux RMS des deux canaux). Le micro est sur l'entrée Aux gauche,
      dupliquée sur les deux canaux DAI par `audio-setup.sh` — si la piste droite ressort
      muette, le second écouteur n'entendra rien en mode restitution.
- [ ] Parole clairement audible, sans saturation ni souffle excessif (ALC bien désactivé).

## 4ter. Choix des fichiers audio depuis le dashboard

- [ ] `/settings`, section « Fichiers audio » : chaque rôle utilisé porte un badge **« converti »**.
- [ ] Changer le fichier d'un rôle → message de succès, et le fichier correspondant
      de `audio/` a bien un horodatage frais (`ls -l audio/`).
- [ ] « Tout reconvertir » régénère l'ensemble sans erreur.
- [ ] Une conversion lancée combiné décroché est **refusée** avec un message clair
      (garde-fou : décoder sature le Pi et ferait rater un enregistrement en cours).

## 5. Parcours nominal (scénario « appel sortant »)

- [ ] Composer chaque chiffre de 0 à 9 → le bon message des mariés est joué à chaque fois.
- [ ] Composer un chiffre sans message attribué → le message générique est joué (pas de silence).
- [ ] Raccrocher pendant la tonalité, pendant un message, et pendant le bip → la lecture s'arrête immédiatement à chaque fois, retour à l'attente.

## 5bis. Scénario « appel entrant »

- [ ] Déclencher la sonnerie depuis le dashboard (`/` → « Sonner maintenant ») et décrocher **pendant** la sonnerie → message aléatoire joué **sans tonalité ni cadran actif**, puis bip + enregistrement.
- [ ] Décrocher dans les `RING_ANSWER_GRACE_SEC` (défaut 5 s) **après la fin** de la sonnerie → même comportement.
- [ ] Décrocher **après** cette fenêtre → comportement nominal (tonalité + cadran).
- [ ] Répéter l'appel entrant plusieurs fois → jamais le même message des mariés deux fois de suite.

## 6. Enregistrement et synchronisation

- [ ] Enregistrer un message test → le fichier WAV apparaît dans `messages/`, horodaté, lisible.
- [ ] Attendre le prochain cycle `rclone-sync.timer` (ou déclencher `/rclone` → « Synchroniser maintenant ») → le fichier test apparaît sur le Google Drive configuré.
- [ ] Les deux dossiers Drive (enregistrements et sources) sont **distincts et non imbriqués** ; le dashboard refuse toute autre saisie.
- [ ] Synchronisation bidirectionnelle **initialisée avant l'événement** : `/rclone`
      affiche « active » (et non « à initialiser »). Sinon, bouton « Réinitialiser la
      synchronisation bidirectionnelle » — ce premier passage peut être long.
- [ ] **Aller-retour smartphone** : déposer un son dans le dossier Drive des sources
      → au cycle suivant il apparaît dans `audio_src/` et dans les listes déroulantes
      de `/settings`. Déposer un fichier localement dans `audio_src/` → il remonte sur le Drive.
- [ ] Après plusieurs cycles, `messages/` **n'a rien perdu** : la copie montante ne
      supprime jamais rien, et les enregistrements ne sont jamais en bisync.
- [ ] `logs/rclone.log` ne contient ni « Bisync critical error » répété, ni avertissement
      de version rclone trop ancienne.

## 7. Réseau : bascule WiFi → AP

- [ ] Débrancher/désactiver le WiFi habituel → le point d'accès `Livre-dor-Mariage` apparaît en quelques dizaines de secondes.
- [ ] Se connecter à cet AP depuis un smartphone → `http://192.168.4.1:5000/` accessible, puis `http://livredor.local:5000/` également.

## 7bis. Réseau : anti-« wifi zombie »

- [ ] Connecter le Pi à un réseau WiFi puis l'éloigner (ou couper la passerelle de ce réseau) → après ~90 s (3 × 30 s), bascule automatique en AP.
- [ ] Vérifier dans `logs/reseau.log` que le SSID fautif est bien blacklisté (~10 min) et que la bascule est journalisée clairement.
- [ ] Le dashboard reste accessible via l'AP pendant toute la durée du test.

## 8. Redémarrage à froid

- [ ] Couper puis rétablir l'alimentation du Pi.
- [ ] Tous les services redémarrent seuls (`systemctl status livre-dor.service dashboard.service wifi-or-ap.timer`).
- [ ] Le dashboard redemande bien le mot de passe (session non restaurée automatiquement pour un nouveau navigateur).
- [ ] Une session déjà ouverte dans un navigateur resté ouvert reste valide (clé de session persistée).

## 9. Test de charge **(auto : outil fourni)**

```bash
python3 src/load_test.py --cycles 20
```

- [ ] Exécuté sur le matériel final ; aucune fuite de sous-processus (`aplay`/`arecord` orphelins) signalée.
- [ ] Nombre de fichiers créés dans `messages/` conforme au nombre de cycles (aucun fichier écrasé ni manquant).

## 9bis. Mode restitution (§5.7) — **après l'événement**

```bash
python3 src/restitution_test.py    # (auto : logique validée sans matériel)
python3 tests/run_tous.py          # (auto : les 8 tests unitaires, sans matériel)
```

Puis, sur le matériel, avec quelques messages déjà présents dans `messages/` :

- [ ] Mode activé depuis le dashboard (page **Mode**) ; l'accueil affiche « restitution »
      et le bouton « Sonner maintenant » est désactivé.
- [ ] Décroché : tonalité présente **immédiatement**, un 440 Hz continu et **sans ondulation** ; elle tient tant qu'on ne compose rien (laisser le combiné décroché ~40 s : elle ne doit pas s'interrompre au bout de 30 s).
- [ ] Elle est coupée **dès la première impulsion** du cadran, pas au chiffre complet, et ne repart pas ensuite. Le journal le confirme : `Lecture tonalite.wav : interrupted en N s`. Si elle se coupe sans qu'on ait touché au cadran, chercher les `impulsion ignorée` / `impulsion comptée` juste avant.
- [ ] Pendant la rotation : **aucun son** — un cadran rotatif n'émet aucun signal audio, la numérotation est purement mécanique.
- [ ] `1` puis attente (~3 s) → le **premier** message enregistré est lu.
- [ ] Quatre chiffres (ex. `9999`) → la lecture démarre **sans attendre**, et un
      cinquième chiffre composé reste sans effet.
- [ ] Numéro supérieur au nombre de messages → **le dernier** message est lu.
- [ ] Raccroché en pleine lecture → arrêt immédiat, aucun `aplay` orphelin (`pgrep aplay`).
- [ ] Combiné laissé décroché plusieurs minutes : **aucune sonnerie** ne se déclenche.
- [ ] Après une session de restitution complète, `messages/` **ne contient aucun
      fichier nouveau ni modifié** (`ls -l messages/`) — le mode est en lecture seule.
- [ ] Écoute : niveau correct dans **les deux** écouteurs, rien ne sort du
      haut-parleur de sonnerie (la lecture est commutée sur la sortie casque, §4.1).
      Si le second écouteur reste muet sur les enregistrements récents, reprendre
      le point 4bis ; `RESTITUTION_SOUND_CARD` reste l'échappatoire de routage.
- [ ] Les enregistrements antérieurs au passage en 48 kHz (mono 44,1 kHz) restent lisibles.
- [ ] Retour en mode mariage depuis le dashboard : le parcours d'origine
      (message des mariés, bip, enregistrement) fonctionne de nouveau.

## 10. Espace disque et stockage

- [ ] Espace disque disponible vérifié (`df -h`), largement au-dessus du seuil d'alerte (500 Mo).
      **Attention** : un enregistrement stéréo 48 kHz pèse 11,5 Mo/min, soit 2,17 fois
      l'ancien format mono 44,1 kHz. Compter ~4,6 Go pour 200 messages de 2 min.
- [ ] `logs/livre_dor.log`, `logs/reseau.log`, `logs/rclone.log` tous non vides et lisibles.

## 11. Sécurité

- [ ] Mot de passe dashboard changé depuis la valeur par défaut (`python3 src/set_password.py`).
- [ ] Mot de passe admin défini si souhaité (`python3 src/set_admin_password.py`), connu de vous seul.
- [ ] Mot de passe de l'AP (`AP_PASSWORD`) changé depuis la valeur par défaut.
- [ ] `secret_key.txt` et `dashboard_config.json` présents avec permissions restreintes (`ls -l`).

## 12. QR codes

- [ ] `/qr` accessible (après connexion) ; le QR de l'URL du dashboard s'ouvre bien sur un smartphone.
- [ ] `/qr/label` génère une étiquette imprimable à la bonne taille physique (mesurer après impression).
- [ ] En mode AP, le QR WiFi de secours apparaît bien sur `/qr` et permet de rejoindre le point d'accès.

---

## Signature

| Rôle | Nom | Date | Remarques |
|---|---|---|---|
| Installateur/trice | | | |
| Témoin (2ᵉ personne) | | | |

Une fois toutes les cases cochées sans intervention manuelle imprévue, le système est jugé **prêt pour l'événement**.
