#!/usr/bin/env python3
"""Lance tous les tests unitaires de tests/ à la suite et affiche un bilan.

Chaque test reste indépendant : il est exécuté dans son propre processus
Python, exactement comme s'il était lancé à la main. Un test en échec
n'empêche pas les suivants de s'exécuter.

Usage :
    python3 tests/run_tous.py               # tous les tests simulés
    python3 tests/run_tous.py --reel        # recette physique complète (§7.6)
    python3 tests/run_tous.py --seulement decroche raccroche
"""

import subprocess
import sys
import time
from pathlib import Path

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import GRAS, GRIS, RAZ, ROUGE, VERT

DOSSIER = Path(__file__).resolve().parent

# Ordre de la recette : les entrées d'abord (ce que le téléphone perçoit),
# puis les sorties (ce qu'il produit), puis les fonctions de service.
ORDRE = [
    "test_decroche.py",
    "test_raccroche.py",
    "test_composition.py",
    "test_lecture.py",
    "test_enregistrement.py",
    "test_sonnerie.py",
    "test_mode.py",
    "test_status.py",
]

# Tests sans mode --reel : ils n'ont rien à valider sur le matériel.
SANS_MODE_REEL = {"test_mode.py"}

# Scripts interactifs : ils attendent une saisie au clavier et n'ont donc pas
# leur place dans une recette automatique, où ils échouent sur l'entrée vide.
# Ils restent lançables à la main, et par --seulement.
INTERACTIFS = {"test_affichage_events.py"}


def scripts(filtre) -> list:
    connus = ORDRE + sorted(p.name for p in DOSSIER.glob("test_*.py")
                            if p.name not in ORDRE and p.name not in INTERACTIFS)
    if not filtre:
        return connus
    connus = connus + sorted(INTERACTIFS)
    choisis = []
    for motif in filtre:
        nom = motif if motif.endswith(".py") else f"test_{motif}.py"
        if nom not in connus:
            raise SystemExit(f"Test inconnu : {motif} (connus : "
                             + ", ".join(n[5:-3] for n in connus) + ")")
        choisis.append(nom)
    return choisis


def main() -> None:
    parser = harness.parseur(__doc__)
    parser.add_argument("--seulement", nargs="+", metavar="NOM", default=None,
                         help="Ne lance que ces tests (ex. : decroche sonnerie).")
    args = parser.parse_args()

    resultats = []
    debut = time.monotonic()
    for nom in scripts(args.seulement):
        if args.reel and nom in SANS_MODE_REEL:
            resultats.append((nom, None))
            continue
        commande = [sys.executable, str(DOSSIER / nom)]
        if args.reel:
            commande.append("--reel")
        if args.verbeux:
            commande.append("--verbeux")
        code = subprocess.run(commande).returncode
        resultats.append((nom, code))

    duree = time.monotonic() - debut
    print(f"\n{GRAS}{'=' * 70}{RAZ}")
    print(f"{GRAS} BILAN{RAZ}")
    print(f"{GRAS}{'=' * 70}{RAZ}")
    echecs = 0
    for nom, code in resultats:
        libelle = nom[5:-3]
        if code is None:
            print(f"  {GRIS}~ {libelle:<20} (sans objet sur matériel){RAZ}")
        elif code == 0:
            print(f"  {VERT}✔{RAZ} {libelle:<20} {GRIS}succès{RAZ}")
        else:
            echecs += 1
            print(f"  {ROUGE}✘{RAZ} {GRAS}{libelle:<20}{RAZ} "
                  f"{ROUGE}échec (code {code}){RAZ}")
    total = len([c for _, c in resultats if c is not None])
    print(f"\n{total - echecs}/{total} test(s) au vert en {duree:.0f}s.\n")
    raise SystemExit(1 if echecs else 0)


if __name__ == "__main__":
    main()
