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

## Outils de diagnostic (pas des tests)

Ces deux scripts ne renvoient ni succès ni échec : ils servent à comprendre un
symptôme, en particulier une **lecture erratique des chiffres composés**.

| Script | Ce qu'il montre |
|---|---|
| `debug_pulses.py` | Journal temps réel : chaque impulsion, chaque changement d'état du cadran, chaque chiffre validé |
| `scope_impulsions.py` | Oscilloscope logique : la forme du signal sur la broche d'impulsions, et ce que le service en décode |

### `scope_impulsions.py` — oscilloscope logique du cadran

Une broche GPIO ne rend qu'un **0 ou un 1** : le Raspberry Pi n'a pas de
convertisseur analogique/numérique, la courbe de tension du contact n'est donc
pas mesurable en logiciel (il faut une sonde d'oscilloscope, ou un ADC externe
type MCP3008/ADS1115 branché en parallèle du contact). En revanche, on peut
échantillonner la broche à 10 kHz — un point toutes les 100 µs — et tracer son
état dans le temps : c'est suffisant pour voir les rebonds de contact, la durée
réelle des impulsions et les marges autour du contact off-normal.

```bash
python3 tests/scope_impulsions.py                         # démo, signal synthétique
python3 tests/scope_impulsions.py --numero 190 --rebond-ms 25   # cadran encrassé
python3 tests/scope_impulsions.py --reel --duree 10 --numero 19 # capture réelle
python3 tests/scope_impulsions.py --reel --niveaux              # moniteur de niveaux
python3 tests/scope_impulsions.py --reel --csv /tmp/t.csv --png /tmp/t.png
```

En `--reel`, le script capture pendant `--duree` secondes : décrochez et
composez pendant ce temps, puis donnez avec `--numero` ce que vous avez
réellement composé — il compare.

Le rapport enchaîne :

0. **le verdict de câblage** : niveau au repos de la broche, nombre et durée
   des écarts à ce repos, cadence. C'est le premier à lire — tant qu'aucune
   excursion ne dure ~33 ms, il n'y a pas de train d'impulsions sur la broche,
   et aucun réglage logiciel n'y changera rien ;
1. **le diagramme**, une ligne par broche (`▔` niveau haut 3,3 V, `▁` niveau
   bas 0 V, `│` un front, `╳` plusieurs fronts dans la même colonne, donc un
   rebond), plus un zoom automatique sur la plus grosse salve de rebonds ;
2. **les mesures** par chiffre : durées de fermeture/ouverture du contact,
   cadence en impulsions/s, nombre de fronts parasites, et les marges entre le
   contact off-normal et la première/dernière impulsion ;
3. **le rejeu** de la trace dans les filtres et le `PhoneInputs` du service, à
   la cadence d'échantillonnage du service : ce n'est pas un modèle du
   décodage, c'est le décodage ;
4. **le balayage** de `PULSE_MIN_REPOS_SEC` croisé avec la cadence
   d'échantillonnage, qui donne le palier de réglage où le décodage est juste.

Les causes qu'il permet de trancher :

- **le contact ne délivre rien** — la broche reste sur un niveau, ou n'émet que
  des pointes de quelques centaines de µs au lieu de créneaux de ~33 ms. Test
  décisif : débranchez le fil du contact d'impulsions et lancez
  `--niveaux`. La broche doit remonter à **HAUT 100 %** grâce au pull-up
  interne. Si elle y remonte, le pull-up marche et c'est le contact du cadran
  qui la tient à la masse (mauvaise paire de fils, contact de shunt câblé en
  parallèle, contact encrassé) ; si elle reste basse débranchée, le problème
  est côté Raspberry Pi ;
- **polarité inversée** — la broche est au repos sur le niveau déclaré actif :
  le service croit voir une impulsion permanente. `PULSE_ACTIF_LEVEL` règle la
  polarité du contact d'impulsions indépendamment de `OFFNORMAL_ACTIF_LEVEL` ;
- **contact usé qui grésille** — le repos exigé est trop court, une impulsion
  est comptée deux fois : le balayage indique le palier correct ;
- **repos exigé trop long** — il soude deux impulsions voisines : le chiffre lu
  est trop petit ;
- **course entre les deux contacts** — quand la dernière impulsion arrive à
  moins de ~15 ms du retour au repos du cadran, RPi.GPIO servant chaque broche
  dans son propre thread, le chiffre peut être validé avant que le dernier coup
  ne soit compté. Aucun réglage d'anti-rebond ne corrige ce cas : le script le
  signale explicitement dans « Pistes ».

### Le cas du contact usé, et le réglage qui le rattrape

Sur un cadran usé, le contact **grésille pendant toute la fermeture** — 40 fronts
en 25 ms, relevés sur le téléphone — alors que le repos entre deux impulsions
reste franc. Les entrées ne sont donc plus lues par interruption : un thread
échantillonne les broches à `GPIO_ECHANTILLONNAGE_HZ` (1 kHz) et n'admet un
changement d'état que s'il se maintient, avec **deux durées différentes selon le
sens** (`gpio_io.FiltreContact`) :

```
   PULSE_MIN_ACTIF_SEC  <<  plus courte impulsion réelle (~33 ms à 10 imp/s)
   plus longue micro-coupure  <  PULSE_MIN_REPOS_SEC  <  plus court repos réel
```

L'impulsion s'ouvre vite et ne se clôt qu'après un repos franc : le grésillement
ne la coupe jamais, et deux impulsions voisines ne fusionnent jamais.

C'est le **balayage** qui donne le réglage, sur le signal réel :

```
   repos exigé │        0.5 kHz │        1.0 kHz │        2.0 kHz
         10 ms │              7 │              7 │              7
         12 ms │              6 │              6 │              6
         25 ms │              6 │              6 │              6   <- au centre du palier
         30 ms │              6 │              6 │              6
         35 ms │              4 │              5 │              5
```

Une valeur isolée qui tombe juste ne vaut rien : seul un **palier large**
garantit que le prochain appel sera lu pareil. Réglez au centre du palier, et
vérifiez que le résultat ne dépend pas de la cadence d'échantillonnage — trois
colonnes identiques, c'est le signe que la lecture est robuste.

`--contact-use` reproduit ce signal en simulation, sans matériel :

```bash
python3 tests/scope_impulsions.py --numero 6 --contact-use
```

Le contact off-normal et le crochet passent par le même filtre, avec une
confirmation symétrique (`OFFNORMAL_CONFIRM_SEC`, `HOOK_CONFIRM_SEC`) : leurs
salves de rebonds n'ouvrent plus de rotation fantôme.

## Tests complémentaires déjà présents dans `src/`

- `python3 src/livre_dor.py --test` — affichage temps réel des trois GPIO.
- `python3 src/load_test.py --cycles 20` — test de charge sur le matériel
  final (fuites de sous-processus et de fichiers).
- `python3 src/restitution_test.py` — scénarios complets du mode restitution.
