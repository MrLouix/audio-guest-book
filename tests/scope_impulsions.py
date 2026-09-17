#!/usr/bin/env python3
"""Oscilloscope logique du capteur d'impulsions du cadran (§5.1, §7.6).

Réponse courte à « puis-je sortir la courbe de tension de la broche ? » :
**pas la tension elle-même**. Le SoC du Raspberry Pi n'a aucun convertisseur
analogique/numérique : une broche GPIO ne sait dire que 0 ou 1, après le
trigger de Schmitt de l'entrée (bascule vers 1 au-dessus de ~1,3 V, vers 0
en dessous de ~0,8 V). Pour une vraie courbe de tension il faut une sonde
d'oscilloscope, ou un ADC externe (MCP3008 en SPI, ADS1115 en I²C) branché
en parallèle du contact.

Ce que l'on peut faire — et qui suffit largement pour comprendre une lecture
erratique des chiffres — c'est un **analyseur logique** : échantillonner la
broche très vite (10 kHz par défaut, un point toutes les 100 µs) et tracer
l'état 0/1 en fonction du temps. On y voit tout ce qui fait faussement
compter une impulsion :

- les rebonds de contact (salves de fronts à quelques centaines de µs) ;
- la durée réelle des impulsions (fermeture / ouverture, ~33 ms / ~67 ms
  pour un cadran à 10 impulsions/seconde) ;
- la marge entre la dernière impulsion et le retour au repos du contact
  off-normal, qui décide si le dernier coup est compté ou perdu ;
- ce que l'anti-rebond de RPi.GPIO (DIAL_DEBOUNCE_SEC) garde ou jette.

Le script termine par un **rejeu** de la trace capturée dans la vraie classe
`gpio_io.PhoneInputs`, avec la même logique que `gpio_io.setup` : il affiche
le chiffre que le service aurait lu, à comparer au chiffre réellement
composé, puis balaie les valeurs d'anti-rebond pour trouver celles qui
décodent juste.

Usage :
    python3 tests/scope_impulsions.py                    # démo sur signal synthétique
    python3 tests/scope_impulsions.py --numero 190 --rebond-ms 25
    python3 tests/scope_impulsions.py --reel             # capture sur le Pi câblé
    python3 tests/scope_impulsions.py --reel --duree 8 --csv /tmp/trace.csv --png /tmp/trace.png
"""

import bisect
import math
import os
import random
import sys
import time
from typing import Dict, List, Optional, Tuple

import harness  # règle sys.path : doit précéder les imports de src/

import config   # noqa: E402
import gpio_io  # noqa: E402


# --- Présentation -------------------------------------------------------

def _unicode_supporte() -> bool:
    try:
        "▔▁│╳─".encode(getattr(sys.stdout, "encoding", None) or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


_COULEUR = not os.environ.get("NO_COLOR") and bool(
    getattr(sys.stdout, "isatty", lambda: False)())
_UNICODE = _unicode_supporte()

VERT = "\033[32m" if _COULEUR else ""
ROUGE = "\033[31m" if _COULEUR else ""
JAUNE = "\033[33m" if _COULEUR else ""
GRIS = "\033[90m" if _COULEUR else ""
GRAS = "\033[1m" if _COULEUR else ""
RAZ = "\033[0m" if _COULEUR else ""

HAUT = "▔" if _UNICODE else "-"
BAS = "▁" if _UNICODE else "_"
FRONT = "│" if _UNICODE else "|"
REBOND = "╳" if _UNICODE else "X"
TRAIT = "─" if _UNICODE else "-"
DOUBLE = "═" if _UNICODE else "="

LARGEUR = 78


def titre(texte: str) -> None:
    print()
    print(GRAS + DOUBLE * LARGEUR + RAZ)
    print(GRAS + " " + texte + RAZ)
    print(GRAS + DOUBLE * LARGEUR + RAZ)


def section(texte: str) -> None:
    print(f"\n{GRAS}{texte}{RAZ}")
    print(GRIS + TRAIT * LARGEUR + RAZ)


def info(texte: str) -> None:
    print(f"  {GRIS}·{RAZ} {texte}")


def alerte(texte: str) -> None:
    print(f"  {ROUGE}!{RAZ} {texte}")


def note(texte: str) -> None:
    print(f"  {JAUNE}~{RAZ} {texte}")


def ok(texte: str) -> None:
    print(f"  {VERT}✔{RAZ} {texte}" if _UNICODE else f"  {VERT}OK{RAZ} {texte}")


# --- Trace capturée -----------------------------------------------------

class Signal:
    """Suite de transitions (instant, niveau électrique) d'une broche.

    `transitions[0]` porte toujours l'instant 0 et le niveau initial : ce
    n'est pas un front, seulement l'état de départ. Les fronts sont donc
    `transitions[1:]`.
    """

    def __init__(self, nom: str, pin: Optional[int], niveau_actif: int,
                 libelle: str) -> None:
        self.nom = nom
        self.pin = pin
        self.niveau_actif = niveau_actif
        self.libelle = libelle
        self.transitions: List[Tuple[float, int]] = []
        self._temps: List[float] = []
        self._taille_cache = -1

    def demarrer(self, niveau: int) -> None:
        self.transitions = [(0.0, niveau)]

    def ajouter(self, instant: float, niveau: int) -> None:
        if self.transitions and self.transitions[-1][1] == niveau:
            return
        self.transitions.append((instant, niveau))

    @property
    def fronts(self) -> List[Tuple[float, int]]:
        return self.transitions[1:]

    def _index(self) -> List[float]:
        if self._taille_cache != len(self.transitions):
            self._temps = [t for t, _ in self.transitions]
            self._taille_cache = len(self.transitions)
        return self._temps

    def niveau_a(self, instant: float) -> int:
        """Niveau électrique de la broche à cet instant (0 ou 1)."""
        temps = self._index()
        i = bisect.bisect_right(temps, instant) - 1
        return self.transitions[max(i, 0)][1]

    def fronts_entre(self, debut: float, fin: float) -> List[Tuple[float, int]]:
        temps = self._index()
        i = max(bisect.bisect_left(temps, debut), 1)
        j = bisect.bisect_left(temps, fin)
        return self.transitions[i:j]

    def fronts_vers_actif(self) -> List[float]:
        return [t for t, niveau in self.fronts if niveau == self.niveau_actif]


class Trace:
    """Ce qu'a vu l'analyseur logique pendant une capture."""

    def __init__(self, duree: float, origine: str) -> None:
        self.duree = duree
        self.origine = origine
        self.signaux: Dict[str, Signal] = {}
        self.echantillons = 0
        self.pas_max = 0.0
        self.freq_demandee = 0.0

    def ajouter_signal(self, signal: Signal) -> Signal:
        self.signaux[signal.nom] = signal
        return signal

    @property
    def impulsions(self) -> Signal:
        return self.signaux["impulsions"]

    @property
    def offnormal(self) -> Signal:
        return self.signaux["off-normal"]

    @property
    def freq_effective(self) -> float:
        return self.echantillons / self.duree if self.duree > 0 else 0.0


# --- Capture sur le matériel --------------------------------------------

def capturer_reel(duree: float, frequence: float, avec_crochet: bool) -> Trace:
    """Échantillonne les broches en boucle serrée et n'enregistre que les fronts.

    Stocker les seuls changements d'état (et pas les millions d'échantillons)
    garde la résolution en µs pour une empreinte mémoire négligeable.
    """
    GPIO = gpio_io.GPIO
    if GPIO is None:
        print(f"\n{JAUNE}RPi.GPIO indisponible : --reel doit être lancé sur le "
              f"Raspberry Pi câblé.{RAZ}\n")
        raise SystemExit(2)

    trace = Trace(duree, "matériel")
    trace.freq_demandee = frequence
    signaux = [
        Signal("impulsions", config.DIAL_PULSE_PIN, gpio_io.DIAL_ACTIVE_LEVEL,
               "contact d'impulsions"),
        Signal("off-normal", config.DIAL_OFFNORMAL_PIN, gpio_io.DIAL_ACTIVE_LEVEL,
               "cadran hors repos"),
    ]
    if avec_crochet:
        signaux.append(Signal("crochet", config.HOOK_PIN, gpio_io.HOOK_ACTIVE_LEVEL,
                              "crochet commutateur"))

    GPIO.setmode(GPIO.BCM)
    for signal in signaux:
        GPIO.setup(signal.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        signal.demarrer(GPIO.input(signal.pin))
        trace.ajouter_signal(signal)

    lecture = GPIO.input
    broches = [(s, s.pin, s.transitions[-1][1]) for s in signaux]
    etats = {s.nom: niveau for s, _, niveau in broches}

    periode = 1.0 / frequence
    print(f"\n  {GRAS}Capture en cours — décrochez et composez maintenant "
          f"({duree:.0f} s, Ctrl+C pour arrêter).{RAZ}")

    depart = time.perf_counter()
    prochain = depart + periode
    precedent = depart
    try:
        while True:
            maintenant = time.perf_counter()
            ecoule = maintenant - depart
            if ecoule >= duree:
                break
            for signal, pin, _ in broches:
                niveau = lecture(pin)
                if niveau != etats[signal.nom]:
                    etats[signal.nom] = niveau
                    signal.ajouter(ecoule, niveau)
            trace.echantillons += 1
            pas = maintenant - precedent
            if pas > trace.pas_max:
                trace.pas_max = pas
            precedent = maintenant
            # Attente active en dessous de 0,5 ms : time.sleep() n'est pas
            # assez fin pour tenir 10 kHz, et une capture de quelques
            # secondes peut se permettre de monopoliser un cœur.
            reste = prochain - time.perf_counter()
            if reste > 0.0005:
                time.sleep(reste - 0.0003)
            while time.perf_counter() < prochain:
                pass
            prochain += periode
    except KeyboardInterrupt:
        print(f"\n  {GRIS}capture interrompue{RAZ}")
    finally:
        trace.duree = time.perf_counter() - depart
        GPIO.cleanup()
    return trace


# --- Capture simulée (poste de développement) ---------------------------

def _front_rebondissant(signal: Signal, instant: float, niveau: int,
                        nb_rebonds: int, duree_rebond: float,
                        alea: random.Random) -> float:
    """Écrit un front avec sa salve de rebonds ; retourne l'instant stabilisé."""
    signal.ajouter(instant, niveau)
    if nb_rebonds <= 0 or duree_rebond <= 0:
        return instant
    instants = sorted(alea.uniform(1e-4, duree_rebond) for _ in range(nb_rebonds))
    courant = niveau
    for decalage in instants:
        courant = 1 - courant
        signal.ajouter(instant + decalage, courant)
    if courant != niveau:
        signal.ajouter(instant + duree_rebond, niveau)
    return instant + duree_rebond


def capturer_simule(numero: str, nb_rebonds: int, duree_rebond: float,
                    impulsions_par_sec: float, marge_fin: float,
                    graine: int) -> Trace:
    """Fabrique une trace crédible : cadran à 10 imp/s, contacts qui rebondissent."""
    alea = random.Random(graine)
    actif = gpio_io.DIAL_ACTIVE_LEVEL
    repos = 1 - actif

    pulse = Signal("impulsions", config.DIAL_PULSE_PIN, actif, "contact d'impulsions")
    offnormal = Signal("off-normal", config.DIAL_OFFNORMAL_PIN, actif, "cadran hors repos")
    crochet = Signal("crochet", config.HOOK_PIN, gpio_io.HOOK_ACTIVE_LEVEL,
                     "crochet commutateur")
    pulse.demarrer(repos)
    offnormal.demarrer(repos)
    crochet.demarrer(gpio_io.HOOK_ACTIVE_LEVEL)  # décroché pendant toute la capture

    periode = 1.0 / impulsions_par_sec
    fermeture = periode / 3.0      # contact fermé ~33 ms à 10 imp/s
    ouverture = periode - fermeture

    t = 0.25
    for caractere in numero:
        if not caractere.isdigit():
            continue
        chiffre = int(caractere)
        _front_rebondissant(offnormal, t, actif, nb_rebonds, duree_rebond, alea)
        t += 0.09 + alea.uniform(-0.01, 0.01)   # retour du cadran avant le 1er coup
        coups = 10 if chiffre == 0 else chiffre
        for rang in range(coups):
            _front_rebondissant(pulse, t, actif, nb_rebonds, duree_rebond, alea)
            t += fermeture * alea.uniform(0.95, 1.05)
            _front_rebondissant(pulse, t, repos, nb_rebonds, duree_rebond, alea)
            if rang < coups - 1:
                t += ouverture * alea.uniform(0.95, 1.05)
        # marge_fin se compte depuis le dernier front du contact : c'est cette
        # marge-là que le retour au repos du cadran vient concurrencer.
        t += marge_fin
        _front_rebondissant(offnormal, t, repos, nb_rebonds, duree_rebond, alea)
        t += 0.30                                # pause entre deux chiffres

    trace = Trace(t + 0.25, "simulation")
    trace.freq_demandee = 10000.0
    trace.echantillons = int(trace.duree * 10000)
    trace.pas_max = 1e-4
    for signal in (pulse, offnormal, crochet):
        trace.ajouter_signal(signal)
    return trace


# --- Tracé « oscilloscope » en mode texte -------------------------------

def tracer(trace: Trace, debut: float, fin: float, colonnes: int) -> None:
    """Dessine la fenêtre [debut, fin] : une ligne par signal, temps en abscisse."""
    pas = (fin - debut) / colonnes
    largeur_nom = max(len(nom) for nom in trace.signaux) + 1
    print(f"\n{GRIS}{TRAIT * (largeur_nom + colonnes + 2)}{RAZ}")
    print(f"{GRAS}{debut * 1000:.1f} ms {TRAIT}> {fin * 1000:.1f} ms{RAZ}"
          f"   {GRIS}{pas * 1000:.3f} ms/colonne{RAZ}")
    for nom, signal in trace.signaux.items():
        cellules = []
        for i in range(colonnes):
            t0 = debut + i * pas
            t1 = t0 + pas
            fronts = signal.fronts_entre(t0, t1)
            if len(fronts) > 1:
                cellules.append(ROUGE + REBOND + RAZ)
            elif len(fronts) == 1:
                cellules.append(JAUNE + FRONT + RAZ)
            else:
                cellules.append(HAUT if signal.niveau_a(t0) == 1 else BAS)
        etiquette = f"{nom:>{largeur_nom}}"
        print(f"{etiquette} {FRONT}{''.join(cellules)}")
    # Règle des temps : un repère toutes les 10 colonnes.
    regle = [" "] * colonnes
    graduations = [" "] * colonnes
    for i in range(0, colonnes, 10):
        regle[i] = "'"
        libelle = f"{(debut + i * pas) * 1000:.0f}"
        for k, caractere in enumerate(libelle):
            if i + k < colonnes:
                graduations[i + k] = caractere
    print(f"{' ' * largeur_nom} {FRONT}{''.join(regle)}")
    print(f"{' ' * largeur_nom} {GRIS}{''.join(graduations)} ms{RAZ}")


def tracer_tout(trace: Trace, colonnes: int, ms_par_ligne: float) -> None:
    fenetre = ms_par_ligne / 1000.0
    nb_lignes = max(1, math.ceil(trace.duree / fenetre))
    for i in range(nb_lignes):
        debut = i * fenetre
        fin = min(debut + fenetre, trace.duree)
        # On saute les fenêtres sans aucun front ni niveau actif : inutile de
        # faire défiler des secondes de silence.
        interessante = any(signal.fronts_entre(debut, fin) for signal in trace.signaux.values())
        if not interessante:
            continue
        tracer(trace, debut, fin, colonnes)
    print(f"\n  {GRIS}{HAUT} niveau haut (3,3 V) · {BAS} niveau bas (0 V) · "
          f"{JAUNE}{FRONT}{RAZ}{GRIS} un front · {ROUGE}{REBOND}{RAZ}{GRIS} "
          f"plusieurs fronts dans la colonne (rebond){RAZ}")


# --- Nettoyage et mesures -----------------------------------------------

def filtrer(signal: Signal, duree: float, seuil: float) -> Signal:
    """Copie du signal privée des états plus courts que `seuil` (les rebonds)."""
    propre = Signal(signal.nom, signal.pin, signal.niveau_actif, signal.libelle)
    propre.demarrer(signal.transitions[0][1])
    for i in range(1, len(signal.transitions)):
        instant, niveau = signal.transitions[i]
        fin = signal.transitions[i + 1][0] if i + 1 < len(signal.transitions) else duree
        if fin - instant < seuil:
            continue
        propre.ajouter(instant, niveau)
    return propre


def fenetres_cadran(offnormal: Signal, duree: float) -> List[Tuple[float, float]]:
    """Intervalles pendant lesquels le cadran est hors de sa position de repos."""
    fenetres = []
    debut = 0.0 if offnormal.transitions[0][1] == offnormal.niveau_actif else None
    for instant, niveau in offnormal.fronts:
        if niveau == offnormal.niveau_actif and debut is None:
            debut = instant
        elif niveau != offnormal.niveau_actif and debut is not None:
            fenetres.append((debut, instant))
            debut = None
    if debut is not None:
        fenetres.append((debut, duree))
    return fenetres


def mesurer(trace: Trace, seuil_glitch: float) -> Tuple[List[int], List[str]]:
    """Décrit le signal propre : chiffres réellement composés + remarques."""
    duree = trace.duree
    pulse = filtrer(trace.impulsions, duree, seuil_glitch)
    offnormal = filtrer(trace.offnormal, duree, seuil_glitch)
    remarques: List[str] = []
    chiffres: List[int] = []

    fenetres = fenetres_cadran(offnormal, duree)
    if not fenetres:
        remarques.append("aucun mouvement du cadran détecté sur le contact off-normal")
        return chiffres, remarques

    for index, (debut, fin) in enumerate(fenetres, start=1):
        coups = [t for t in pulse.fronts_vers_actif() if debut <= t < fin]
        chiffre = len(coups) % 10
        chiffres.append(chiffre)
        section(f"Chiffre n°{index} — cadran hors repos pendant "
                f"{(fin - debut) * 1000:.0f} ms")
        info(f"{len(coups)} impulsion(s) → chiffre {chiffre}")
        if not coups:
            remarques.append(f"chiffre n°{index} : aucune impulsion dans la fenêtre du cadran")
            continue

        fermetures, ouvertures, periodes = [], [], []
        for t in coups:
            suite = [x for x, niveau in pulse.fronts
                     if x > t and niveau != pulse.niveau_actif]
            if suite:
                fermetures.append(suite[0] - t)
                reouverture = [x for x in pulse.fronts_vers_actif() if x > suite[0]]
                if reouverture and reouverture[0] < fin:
                    ouvertures.append(reouverture[0] - suite[0])
                    periodes.append(reouverture[0] - t)
        if fermetures:
            info(f"contact fermé  : {min(fermetures)*1000:.1f} → "
                 f"{max(fermetures)*1000:.1f} ms (moyenne {sum(fermetures)/len(fermetures)*1000:.1f})")
        if ouvertures:
            info(f"contact ouvert : {min(ouvertures)*1000:.1f} → "
                 f"{max(ouvertures)*1000:.1f} ms (moyenne {sum(ouvertures)/len(ouvertures)*1000:.1f})")
        if periodes:
            cadence = len(periodes) / sum(periodes)
            info(f"cadence : {cadence:.1f} impulsions/s "
                 f"(période {sum(periodes)/len(periodes)*1000:.1f} ms)")

        # La marge de fin se mesure sur le *dernier front* du contact (fin de
        # la dernière impulsion), pas sur sa fermeture : c'est lui qui court
        # après le retour au repos du contact off-normal.
        derniers = [t for t, _ in pulse.fronts if debut <= t < fin]
        marge_debut = (coups[0] - debut) * 1000
        marge_fin = (fin - derniers[-1]) * 1000
        info(f"marge avant la 1re impulsion : {marge_debut:.1f} ms · "
             f"après la dernière : {marge_fin:.1f} ms")
        if marge_debut < 15:
            remarques.append(
                f"chiffre n°{index} : seulement {marge_debut:.1f} ms entre le départ du "
                f"cadran et la 1re impulsion — si le callback off-normal arrive après "
                f"cette impulsion, elle est ignorée (chiffre trop petit d'une unité)")
        if marge_fin < 15:
            remarques.append(
                f"chiffre n°{index} : seulement {marge_fin:.1f} ms entre la dernière "
                f"impulsion et le retour au repos — RPi.GPIO servant chaque broche dans "
                f"son propre thread, le chiffre peut être validé avant que le dernier "
                f"coup ne soit compté")

        # Rebonds : fronts du signal brut supprimés par le filtre.
        bruts = len(trace.impulsions.fronts_entre(debut, fin))
        propres = len(pulse.fronts_entre(debut, fin))
        if bruts > propres:
            note(f"{bruts - propres} front(s) parasite(s) sur {bruts} — "
                 f"rebonds de contact")
    return chiffres, remarques


def pires_rebonds(signal: Signal, seuil: float) -> List[Tuple[float, float, int]]:
    """Salves de fronts espacés de moins de `seuil` : (début, fin, nombre)."""
    salves = []
    courant: List[float] = []
    for instant, _ in signal.fronts:
        if courant and instant - courant[-1] <= seuil:
            courant.append(instant)
        else:
            if len(courant) > 1:
                salves.append((courant[0], courant[-1], len(courant)))
            courant = [instant]
    if len(courant) > 1:
        salves.append((courant[0], courant[-1], len(courant)))
    salves.sort(key=lambda s: s[2], reverse=True)
    return salves


# --- Rejeu dans la vraie machine à états --------------------------------

def rejouer(trace: Trace, anti_rebond: float, latence: float) -> List[int]:
    """Décode la trace exactement comme `gpio_io.setup` le ferait.

    Reproduit les deux mécanismes de RPi.GPIO qui décident du résultat :
    `bouncetime` (tout front survenant moins de N ms après le dernier front
    *accepté* sur la même broche est jeté) et la relecture du niveau dans le
    callback, qui a lieu un peu après le front — d'où `latence`.
    """
    inputs = gpio_io.PhoneInputs()
    pulse = trace.impulsions
    offnormal = trace.offnormal

    inputs.set_hook(True)
    inputs.set_dial_active(offnormal.niveau_a(0.0) == gpio_io.DIAL_ACTIVE_LEVEL)

    evenements = [(t, "off-normal") for t, _ in offnormal.fronts]
    evenements += [(t, "impulsions") for t, _ in pulse.fronts]
    evenements.sort()

    dernier = {"off-normal": -math.inf, "impulsions": -math.inf}
    chiffres: List[int] = []
    for instant, nom in evenements:
        if instant - dernier[nom] < anti_rebond:
            continue
        dernier[nom] = instant
        if nom == "off-normal":
            niveau = offnormal.niveau_a(instant + latence)
            inputs.set_dial_active(niveau == gpio_io.DIAL_ACTIVE_LEVEL)
        else:
            if pulse.niveau_a(instant + latence) == gpio_io.DIAL_ACTIVE_LEVEL:
                inputs.register_pulse()
        chiffre = inputs.pop_digit()
        while chiffre is not None:
            chiffres.append(chiffre)
            chiffre = inputs.pop_digit()
    chiffre = inputs.pop_digit()
    while chiffre is not None:
        chiffres.append(chiffre)
        chiffre = inputs.pop_digit()
    return chiffres


def balayer(trace: Trace, attendu: List[int]) -> None:
    """Table anti-rebond × latence du callback → chiffres décodés."""
    valeurs = [0, 1, 2, 3, 5, 8, 10, 15, 20, 25, 30, 40]
    latences = [0.0, 0.002, 0.005]
    actuel = int(round(config.DIAL_DEBOUNCE_SEC * 1000))
    reference = "".join(str(c) for c in attendu)

    section("Balayage de l'anti-rebond (DIAL_DEBOUNCE_SEC)")
    info(f"référence : {reference or '—'}")
    entete = f"  {'anti-rebond':>12} │ " + " │ ".join(
        f"latence {l * 1000:.0f} ms".rjust(16) for l in latences)
    print(GRIS + entete + RAZ)
    bons: List[int] = []
    for ms in valeurs:
        cellules = []
        for latence in latences:
            lus = "".join(str(c) for c in rejouer(trace, ms / 1000.0, latence))
            juste = lus == reference
            if juste and latence == 0.0:
                bons.append(ms)
            couleur = VERT if juste else ROUGE
            cellules.append(f"{couleur}{(lus or '—'):>16}{RAZ}")
        marque = f" {GRAS}<< config actuelle{RAZ}" if ms == actuel else ""
        print(f"  {ms:>9} ms │ " + " │ ".join(cellules) + marque)

    print()
    if bons:
        ok(f"anti-rebond correct entre {min(bons)} et {max(bons)} ms "
           f"(valeur actuelle : {actuel} ms)")
        if actuel not in bons:
            alerte(f"DIAL_DEBOUNCE_SEC = {config.DIAL_DEBOUNCE_SEC} s décode faux sur "
                   f"cette trace : essayez {(min(bons) + max(bons)) // 2 / 1000:.3f} s "
                   f"(page /settings du dashboard, ou variable d'environnement).")
    else:
        alerte("aucune valeur d'anti-rebond ne décode le bon numéro : le problème "
               "n'est pas (que) l'anti-rebond — regardez les marges off-normal "
               "ci-dessus et la relecture du niveau dans le callback.")


# --- Exports ------------------------------------------------------------

def exporter_csv(trace: Trace, chemin: str) -> None:
    import csv
    with open(chemin, "w", newline="", encoding="utf-8") as fichier:
        ecrivain = csv.writer(fichier)
        ecrivain.writerow(["temps_ms", "signal", "niveau", "etat"])
        lignes = []
        for nom, signal in trace.signaux.items():
            for instant, niveau in signal.transitions:
                etat = "actif" if niveau == signal.niveau_actif else "repos"
                lignes.append((instant * 1000, nom, niveau, etat))
        lignes.sort()
        for ligne in lignes:
            ecrivain.writerow([f"{ligne[0]:.3f}", ligne[1], ligne[2], ligne[3]])
    info(f"transitions écrites dans {chemin}")


def exporter_png(trace: Trace, chemin: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        note("matplotlib absent : pas d'export PNG "
             "(pip3 install matplotlib, ou utilisez --csv).")
        return

    signaux = list(trace.signaux.items())
    figure, axes = plt.subplots(figsize=(14, 1.2 * len(signaux) + 1.6))
    positions, etiquettes = [], []
    # Ordre inversé : le premier signal (les impulsions) se retrouve en haut.
    for rang, (nom, signal) in enumerate(reversed(signaux)):
        decalage = rang * 1.6
        temps = [t * 1000 for t, _ in signal.transitions] + [trace.duree * 1000]
        niveaux = [n for _, n in signal.transitions]
        niveaux.append(niveaux[-1])
        axes.step(temps, [n + decalage for n in niveaux], where="post", linewidth=1.2)
        positions.append(decalage + 0.5)
        etiquettes.append(f"{nom}\n(actif = {signal.niveau_actif})")
    axes.set_xlabel("temps (ms)")
    axes.set_yticks(positions)
    axes.set_yticklabels(etiquettes, fontsize=8)
    axes.set_ylim(-0.4, (len(signaux) - 1) * 1.6 + 1.4)
    axes.set_title(f"Cadran rotatif — trace logique ({trace.origine}, "
                   f"{trace.duree:.2f} s)")
    axes.grid(axis="x", alpha=0.3)
    figure.tight_layout()
    figure.savefig(chemin, dpi=130)
    plt.close(figure)
    info(f"diagramme écrit dans {chemin}")


# --- Programme ----------------------------------------------------------

def main() -> None:
    parseur = harness.parseur(__doc__)
    parseur.add_argument("--duree", type=float, default=10.0,
                         help="Durée de la capture en secondes (--reel).")
    parseur.add_argument("--freq", type=float, default=10000.0,
                         help="Fréquence d'échantillonnage en Hz (--reel, défaut 10000).")
    parseur.add_argument("--colonnes", type=int, default=100,
                         help="Largeur du tracé en caractères (défaut 100).")
    parseur.add_argument("--ms-par-ligne", type=float, default=200.0,
                         dest="ms_par_ligne",
                         help="Durée couverte par une ligne de tracé (défaut 200 ms).")
    parseur.add_argument("--glitch-ms", type=float, default=5.0, dest="glitch_ms",
                         help="Sous cette durée, un état est tenu pour un rebond "
                              "et non pour une vraie impulsion (défaut 5 ms).")
    parseur.add_argument("--sans-trace", action="store_true",
                         help="N'affiche que les mesures, sans le diagramme.")
    parseur.add_argument("--csv", metavar="FICHIER",
                         help="Écrit toutes les transitions dans un CSV.")
    parseur.add_argument("--png", metavar="FICHIER",
                         help="Écrit le diagramme en PNG (nécessite matplotlib).")
    parseur.add_argument("--numero", default=None,
                         help="Numéro composé, pour comparer au décodage : celui du "
                              "cadran fictif en simulation (défaut 19), celui que vous "
                              "venez de composer en --reel.")
    parseur.add_argument("--rebond-ms", type=float, default=2.0, dest="rebond_ms",
                         help="Simulation : durée des salves de rebond (défaut 2 ms ; "
                              "essayez 25 pour reproduire une lecture erratique).")
    parseur.add_argument("--rebonds", type=int, default=4,
                         help="Simulation : nombre de fronts parasites par front.")
    parseur.add_argument("--imp-par-sec", type=float, default=10.0, dest="imp_par_sec",
                         help="Simulation : cadence du cadran (défaut 10 imp/s).")
    parseur.add_argument("--marge-fin-ms", type=float, default=25.0, dest="marge_fin_ms",
                         help="Simulation : délai entre la dernière impulsion et le "
                              "retour au repos du contact off-normal.")
    parseur.add_argument("--graine", type=int, default=1,
                         help="Simulation : graine aléatoire (reproductibilité).")
    args = parseur.parse_args()
    harness.configurer_logs(args.verbeux)

    titre("OSCILLOSCOPE LOGIQUE — CAPTEUR D'IMPULSIONS DU CADRAN")

    section("Paramètres en vigueur (config.py)")
    for nom in ("DIAL_PULSE_PIN", "DIAL_OFFNORMAL_PIN", "HOOK_PIN",
                "OFFNORMAL_ACTIF_LEVEL", "DIAL_DEBOUNCE_SEC"):
        info(f"{nom} = {getattr(config, nom)!r}   {GRIS}[{harness.provenance(nom)}]{RAZ}")

    if args.reel:
        trace = capturer_reel(args.duree, args.freq, avec_crochet=True)
    else:
        numero_simule = args.numero or "19"
        trace = capturer_simule(numero_simule, args.rebonds, args.rebond_ms / 1000.0,
                                args.imp_par_sec, args.marge_fin_ms / 1000.0,
                                args.graine)
        note(f"Signal synthétique ({numero_simule}) : aucun matériel requis. "
             f"Lancez avec --reel sur le Raspberry Pi pour capturer le vrai cadran.")

    section("Capture")
    info(f"origine : {trace.origine} · durée : {trace.duree:.2f} s")
    if args.reel:
        info(f"{trace.echantillons} échantillons · fréquence effective "
             f"{trace.freq_effective / 1000:.1f} kHz "
             f"(demandée {trace.freq_demandee / 1000:.1f} kHz)")
        info(f"plus grand trou entre deux lectures : {trace.pas_max * 1000:.3f} ms "
             f"— tout événement plus court a pu passer inaperçu")
        if trace.pas_max > 0.002:
            note("la boucle a été préemptée : relancez la capture avec une "
                 "machine moins chargée pour une trace fiable.")
    for nom, signal in trace.signaux.items():
        info(f"{nom:>11} : {len(signal.fronts)} front(s), "
             f"niveau actif = {signal.niveau_actif} "
             f"({'LOW' if signal.niveau_actif == 0 else 'HIGH'})")

    if not trace.impulsions.fronts and not trace.offnormal.fronts:
        alerte("aucun front capturé : le cadran n'a pas bougé, ou le câblage de la "
               "broche d'impulsions est à revoir (vérifiez avec "
               "python3 src/livre_dor.py --test).")
        raise SystemExit(1)

    if not args.sans_trace:
        section("Diagramme (état électrique de chaque broche)")
        tracer_tout(trace, args.colonnes, args.ms_par_ligne)

        salves = pires_rebonds(trace.impulsions, 0.005)
        if salves:
            debut, fin, nombre = salves[0]
            section(f"Zoom sur la plus grosse salve de rebonds ({nombre} fronts "
                    f"en {(fin - debut) * 1000:.2f} ms)")
            marge = max((fin - debut) * 0.6, 0.002)
            tracer(trace, max(debut - marge, 0.0),
                   min(fin + marge, trace.duree), args.colonnes)

    section("Mesures sur le signal nettoyé "
            f"(rebonds < {args.glitch_ms:.0f} ms écartés)")
    chiffres, remarques = mesurer(trace, args.glitch_ms / 1000.0)

    section("Ce que le service aurait lu (rejeu dans gpio_io.PhoneInputs)")
    declare = [int(c) for c in (args.numero or "") if c.isdigit()]
    if not args.reel and not declare:
        declare = [int(c) for c in numero_simule if c.isdigit()]
    # La référence est le numéro annoncé quand on le connaît ; sinon celui que
    # le signal nettoyé permet de reconstruire.
    reference = declare or chiffres
    mesure = "".join(str(c) for c in chiffres)
    attendu = "".join(str(c) for c in reference)
    lus = "".join(str(c) for c in rejouer(trace, config.DIAL_DEBOUNCE_SEC, 0.0))
    if declare:
        info(f"numéro composé (annoncé)  : {GRAS}{attendu or '—'}{RAZ}")
    info(f"reconstruit du signal nettoyé : {GRAS}{mesure or '—'}{RAZ}")
    info(f"décodé avec DIAL_DEBOUNCE_SEC = {config.DIAL_DEBOUNCE_SEC} s : "
         f"{GRAS}{lus or '—'}{RAZ}")
    if declare and mesure != attendu:
        alerte(f"même le signal nettoyé ne redonne pas le bon numéro : les rebonds "
               f"durent plus que le seuil de {args.glitch_ms:.0f} ms, ou les "
               f"impulsions sont plus courtes. Relancez avec --glitch-ms plus haut ; "
               f"si rien ne marche, le contact est à nettoyer/rétablir, aucun "
               f"réglage logiciel ne rattrapera ce signal.")
    if lus == attendu and attendu:
        ok("le décodage est juste sur cette trace")
    else:
        alerte("le décodage diffère du numéro composé : c'est exactement le "
               "symptôme observé sur le téléphone")

    if reference:
        balayer(trace, reference)

    if remarques:
        section("Pistes")
        for remarque in remarques:
            alerte(remarque)

    if args.csv or args.png:
        section("Exports")
        if args.csv:
            exporter_csv(trace, args.csv)
        if args.png:
            exporter_png(trace, args.png)

    section("À savoir sur la mesure")
    info("Une broche GPIO ne rend qu'un 0 ou un 1 : le Raspberry Pi n'a pas d'ADC, "
         "la tension réelle du contact n'est pas mesurable par ce script.")
    info("Pour une vraie courbe de tension : sonde d'oscilloscope, ou ADC externe "
         "(MCP3008 en SPI, ADS1115 en I²C) branché en parallèle du contact.")
    info("Le trigger de Schmitt de l'entrée bascule vers 1 au-dessus de ~1,3 V et "
         "vers 0 en dessous de ~0,8 V : entre les deux, l'état lu est indécis — "
         "c'est là que naissent les impulsions fantômes quand la résistance de "
         "pull-up est trop faible ou le contact encrassé.")

    print()


if __name__ == "__main__":
    main()
