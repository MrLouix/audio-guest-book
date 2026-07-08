# Checklist de mise en service — Livre d'or téléphonique

Basée sur la checklist du §7.6 de la [spécification](specification_livre_dor_telephonique.md). À dérouler intégralement sur le **matériel final assemblé**, avant l'événement. Cocher chaque case au fur et à mesure ; noter la date et qui l'a réalisée en bas de page.

La plupart des points nécessitent le Raspberry Pi, le téléphone câblé et un smartphone — ils ne peuvent pas être vérifiés autrement que physiquement. Les points marqués **(auto)** disposent d'un outil du dépôt qui automatise une partie de la vérification.

---

## 1. Identification de la carte son

- [ ] `aplay -l` et `arecord -l` exécutés sur le Pi ; carte son USB repérée (ex. `card 1`).
- [ ] `config.SOUND_CARD` (ou variable d'environnement `SOUND_CARD`) mis à jour en conséquence (ex. `plughw:1,0`).

## 2. Sens logique des contacts (multimètre)

- [ ] Crochet du combiné testé au multimètre (normalement ouvert ou fermé au repos) ; `HOOK_ACTIVE_STATE` ajusté si besoin.
- [ ] Contact « off-normal » du cadran testé ; `OFFNORMAL_ACTIF_LEVEL` ajusté si besoin.

## 3. Mode `--test` **(auto : outil fourni)**

- [ ] `python3 src/livre_dor.py --test` lancé ; décrocher → l'affichage bascule bien sur « décroché ».
- [ ] Tourner le cadran → l'affichage détecte bien le mouvement et les impulsions.
- [ ] Raccrocher → retour à l'état de repos affiché.

## 4. Test audio

- [ ] Tonalité entendue **uniquement** dans l'écouteur (pas de fuite dans le haut-parleur).
- [ ] Sonnerie entendue **uniquement** dans le haut-parleur externe (pas de fuite dans l'écouteur).
- [ ] Volume du haut-parleur réglé au potentiomètre PAM8403 à un niveau approprié (ni inaudible, ni agressif).

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

## 10. Espace disque et stockage

- [ ] Espace disque disponible vérifié (`df -h`), largement au-dessus du seuil d'alerte (500 Mo).
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
