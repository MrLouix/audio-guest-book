# Le téléphone : parcours, sonnerie et fiabilité

[← README](../README.md) · Spécification : §1.2 (parcours), §5.1 (machine à états), §7 (fiabilité)

`src/livre_dor.py` est la machine à états qui pilote le téléphone.

```bash
python3 src/livre_dor.py --test      # affichage temps réel des 3 GPIO, pour valider le câblage
python3 src/livre_dor.py             # démarre la machine à états (vrai Raspberry Pi)
python3 src/livre_dor.py --verbeux   # idem, avec le détail du cadran (DEBUG)
```

En production, elle tourne comme service systemd (`livre-dor.service`, voir [Réseau, synchro et services](reseau_synchro.md#services-systemd-et-watchdog)).

## Parcours d'un invité (« appel sortant »)

attente → décroché (tonalité) → dès la première impulsion, la tonalité s'arrête → numérotation (impulsions comptées, chiffre validé au retour du cadran au repos, 10 impulsions = 0) → lecture de `audio/message_N.wav` (repli sur `message_generique.wav`) → bip → enregistrement (`messages/message_AAAA-MM-JJ_HH-MM-SS.wav`, jamais d'écrasement) → retour à l'attente.

Toute lecture est interrompue immédiatement au raccroché ; l'enregistrement est plafonné à `MAX_RECORD_SEC`.

### La tonalité, comme sur un poste à cadran

- **Au décroché, la tonalité d'invitation à numéroter** : un **440 Hz continu et non modulé** (et non le 440 + 480 Hz nord-américain, dont le battement à 40 Hz s'entend). Elle est **tenue tant que rien n'est composé** : le fichier de 30 s est rejoué en boucle, sans raccord audible. `TONALITE_MAX_SEC` (défaut 180 s) borne le total ; **0 supprime la tonalité** sans rien changer au reste du parcours.
- **Elle tombe net à la première impulsion**, pas au premier chiffre complet : c'est la coupure de boucle qui la fait taire. Elle ne repart pas entre deux chiffres d'un numéro de restitution.
- **Pendant la composition, plus rien.** Un cadran rotatif ne produit aucun son : la numérotation décimale est mécanique. Les « bip-bip-bip » sont ceux d'un clavier DTMF.

## Sonnerie et « appel entrant »

- **Sonnerie périodique** : en attente, `livre_dor.py` sonne toutes les `RING_INTERVAL_SEC`, comptées depuis la fin de la sonnerie précédente. `audio/ring_out.wav` est rejoué `RING_COUNT` fois (défaut 5), séparé par `RING_PAUSE_SEC` (défaut 2 s).
- **`RING_INTERVAL_SEC = 0` (défaut) coupe la sonnerie périodique** ; 90 s est une valeur typique pour la réactiver. Le bouton « Sonner maintenant » du dashboard continue de fonctionner, et `status.json` porte le détail « sonnerie périodique désactivée », pour qu'une coupure volontaire ne ressemble pas à une panne.
- **Sonnerie à distance** : le dashboard crée le fichier `ring_trigger`, consommé puis supprimé.
- **Appel entrant** : un décroché pendant la sonnerie ou dans les `RING_ANSWER_GRACE_SEC` (défaut 5 s) qui suivent coupe la sonnerie. **Aucune tonalité, cadran ignoré** ; un message est tiré au hasard parmi les `message_N.wav` et `message_generique.wav` disponibles (jamais deux fois de suite le même), puis bip et enregistrement. Hors de cette fenêtre, le décroché suit le parcours habituel.
- Chaque sonnerie terminée republie l'état `attente` : le dashboard ne reste jamais sur « sonnerie ».

## Fiabilité

- **`status.json`** (`status_io.py`) : écrit de façon atomique (temporaire + `os.replace()`) à chaque transition, contrat `{ "etat", "derniere_maj", "detail" }` (§8) ; rafraîchi en attente (`STATUS_HEARTBEAT_SEC`) pour ne jamais paraître périmé au watchdog.
- **Démarrage robuste** : attente de la carte son (`SOUND_CARD`) sans jamais renoncer, état `erreur` affiché en attendant ; refus de démarrer si `bip.wav` ou `message_generique.wav` manquent.
- **Espace disque** : sous `DISK_WARNING_MB` (500 Mo), l'enregistrement est tenté quand même (statut `erreur` signalé) ; sous `DISK_CRITICAL_MB` (100 Mo), il est refusé.
- **Micro-coupures du crochet** : le raccroché doit rester stable `RECORDING_HANGUP_CONFIRM_SEC` avant d'arrêter un enregistrement, pour ne pas tronquer un message sur un faux contact.
- **Enregistrements très courts** (< `SHORT_RECORDING_THRESHOLD_SEC`) : conservés, seulement journalisés.
- **Logs** : `logs/livre_dor.log`, rotation 5 × 1 Mo, en plus de la console.
- **Exception imprévue** : journalisée avec sa trace, `status.json` passe en `erreur`, puis l'exception remonte pour que systemd relance le service ; `GPIO.cleanup()` reste garanti.

`gpio_io.py` isole l'accès matériel derrière `PhoneInputs`, un état partagé indépendant du matériel : toute la logique se vérifie sans Raspberry Pi (voir [tests](../tests/README.md)).

## Mise au point des premiers essais

Tout le parcours est journalisé au niveau **INFO** : décroché, chiffre composé, fichier lu, début et fin d'enregistrement, raccroché. Chaque lecture audio indique aussi **son issue et sa durée** (`Lecture tonalite.wav : interrupted en 4.2 s`), ce qui distingue une tonalité coupée par une impulsion d'une tonalité coupée par un raccroché. Ce qu'`aplay` et `arecord` écrivent sur leur sortie d'erreur est repris quel que soit le résultat.

Le niveau **DEBUG** ouvre le détail du matériel :

```
DEBUG impulsions -> actif (état précédent tenu 118 ms)
DEBUG impulsion comptée (1 depuis le début de la rotation)
DEBUG impulsions -> repos (état précédent tenu 62 ms)
DEBUG impulsion ignorée : le cadran est au repos
DEBUG chiffre validé : 3 (3 impulsion(s) comptée(s))
DEBUG Sortie commutée sur headphone.
```

Les durées d'état règlent `PULSE_MIN_ACTIF_SEC` et `PULSE_MIN_REPOS_SEC` sans arrêter le service. « impulsion ignorée » explique un chiffre perdu ; « rotation sans impulsion », un cadran effleuré.

Pour l'activer : `python3 src/livre_dor.py --verbeux` à la main, ou `LOG_LEVEL = DEBUG` dans `/settings` puis `sudo systemctl restart livre-dor` pour le service. **Repasser à INFO** une fois la mise en service terminée.

Au démarrage, le service récapitule ce qu'il va utiliser ; c'est le premier endroit où regarder devant un « aucun son » :

```
INFO Audio : carte plughw:1,0, sonnerie sur lineout, combiné sur headphone, 48000 Hz 2 canaux ; tonalité bornée à 180 s.
INFO GPIO échantillonnés à 1000 Hz en activité, 50 Hz au repos (crochet=17 actif LOW, ...)
```

Outils dédiés au cadran, hors service : `livre_dor.py --test`, `tests/debug_pulses.py`, `tests/scope_impulsions.py` (rejoue une capture réelle dans le filtre du service) et `tests/test_composition.py --reel`. Détails dans [tests/README.md](../tests/README.md#outils-de-diagnostic-pas-des-tests).
