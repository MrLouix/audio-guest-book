# Tests unitaires par fonction

Un script par fonction du téléphone, **indépendant des autres**, à lancer
seul dans un terminal. Chaque script affiche son propre rapport et sort avec
le code **0** (tout passe) ou **1** (au moins une vérification en échec).

Aucune dépendance à installer : ni `pytest`, ni `flask`, ni `pydub`, ni
`RPi.GPIO`. Les scripts appellent directement les fonctions de `src/` et
utilisent les paramètres déjà configurés (variables d'environnement,
`custom_config.json` écrit par le dashboard, puis valeurs par défaut de
`config.py` — dans cet ordre, celui de `config.py`).

```bash
python3 tests/test_decroche.py          # une fonction
python3 tests/run_tous.py               # toutes, avec un bilan final
```

## Les scripts

| Script | Fonction validée |
|---|---|
| `test_decroche.py` | Décroché : tonalité, purge du cadran, appel entrant, restitution |
| `test_raccroche.py` | Raccroché à chaque étape du parcours, anti-rebond du crochet |
| `test_composition.py` | Cadran rotatif : impulsions → chiffre, numéro multi-chiffres |
| `test_lecture.py` | Lecture d'un message : sélection du fichier, `aplay`, interruption |
| `test_enregistrement.py` | Enregistrement : nom horodaté, `arecord`, espace disque |
| `test_sonnerie.py` | Sonnerie périodique, `ring_trigger`, fenêtre de grâce |
| `test_mode.py` | Bascule mariage / restitution à chaud (`mode_config.json`) |
| `test_status.py` | `status.json`, battement de cœur et décisions du watchdog |

`harness.py` n'est pas un test : c'est la boîte à outils commune (rapport
terminal, téléphone simulé, doublures audio).

## Deux modes d'exécution

**Simulation** (par défaut) — tourne sur n'importe quelle machine, y compris
un poste de développement. Les GPIO sont pilotés à la main via
`gpio_io.PhoneInputs`, `aplay`/`arecord` sont remplacés par des doublures, et
toute l'arborescence de données est redirigée vers un dossier temporaire :
**`messages/`, `audio/` et `status.json` du dépôt ne sont jamais touchés.**

**Matériel réel** (`--reel`) — à lancer sur le Raspberry Pi câblé, avec la
carte son branchée. Ce sont les mêmes fonctions du service qui sont
appelées (`gpio_io.setup`, `audio_io.play`/`record`,
`livre_dor.HangupConfirmer`...), avec les broches, la carte son et les durées
de l'installation ; le script affiche d'abord les paramètres en vigueur et
leur origine. Le test vous demande alors de décrocher, de composer, d'écouter,
et vérifie ce que le matériel répond.

```bash
python3 tests/test_decroche.py --reel        # décrochez / raccrochez à la demande
python3 tests/test_composition.py --reel     # composez les chiffres demandés
python3 tests/test_lecture.py --reel         # écoute : panning, niveau, coupure
python3 tests/test_sonnerie.py --reel        # sonnerie sur le haut-parleur
python3 tests/test_enregistrement.py --reel  # parlez, raccrochez, réécoutez
python3 tests/test_status.py --reel          # état réel publié par le service
python3 tests/run_tous.py --reel             # recette physique complète
```

Les enregistrements faits en mode `--reel` vont dans un dossier temporaire,
supprimé à la fin : ils ne se mélangent jamais aux messages des invités.

## Options communes

| Option | Effet |
|---|---|
| `--reel` | Teste le matériel au lieu de la simulation |
| `--verbeux` | Affiche aussi les logs du service (diagnostic d'un échec) |
| `--duree N` | `test_enregistrement.py` : durée de la prise de son (`--reel`) |
| `--fichier F` | `test_lecture.py` : joue ce WAV plutôt que ceux du parcours |
| `--seulement …` | `run_tous.py` : ne lance que les tests nommés |

## Lire le rapport

```
▶ 4. Raccroché pendant la lecture du message des mariés
  ✔ le message du chiffre composé démarre
  ✘ la lecture est interrompue
        interrompus : []
  · 3 message(s) disponible(s)
```

`✔` vérification passée · `✘` échec, avec le détail observé en dessous ·
`~` vérification ignorée (matériel absent) · `·` information.

Un échec ne masque pas les suivants : le script va au bout et récapitule
toutes les vérifications en échec à la fin.

## Tests complémentaires déjà présents dans `src/`

- `python3 src/livre_dor.py --test` — affichage temps réel des trois GPIO.
- `python3 src/load_test.py --cycles 20` — test de charge sur le matériel
  final (fuites de sous-processus et de fichiers).
- `python3 src/restitution_test.py` — scénarios complets du mode restitution.
