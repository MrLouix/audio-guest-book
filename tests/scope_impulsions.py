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
        Signal("impulsions", config.DIAL_PULSE_PIN, gpio_io.PULSE_ACTIVE_LEVEL,
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
                    graine: int, contact_use: bool = False) -> Trace:
    """Fabrique une trace crédible : cadran à 10 imp/s, contacts qui rebondissent.

    Avec contact_use, le contact d'impulsions grésille pendant toute la
    fermeture et l'une d'elles porte une coupure longue — le signal relevé sur
    un cadran usé, celui que le filtre doit savoir lire (harness.segments_cadran_use).
    """
    alea = random.Random(graine)
    actif = gpio_io.DIAL_ACTIVE_LEVEL
    repos = 1 - actif
    actif_pulse = gpio_io.PULSE_ACTIVE_LEVEL

    pulse = Signal("impulsions", config.DIAL_PULSE_PIN, actif_pulse, "contact d'impulsions")
    offnormal = Signal("off-normal", config.DIAL_OFFNORMAL_PIN, actif, "cadran hors repos")
    crochet = Signal("crochet", config.HOOK_PIN, gpio_io.HOOK_ACTIVE_LEVEL,
                     "crochet commutateur")
    pulse.demarrer(1 - actif_pulse)
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
        if contact_use:
            # Proportions relevées sur le cadran réel : ~62 % fermé, ~38 % ouvert.
            segments = harness.segments_cadran_use(
                coups, fermeture=periode * 0.62, repos=periode * 0.38,
                graine=graine + chiffre)
            for duree, est_actif in segments:
                pulse.ajouter(t, actif_pulse if est_actif else 1 - actif_pulse)
                t += duree
            t -= periode * 0.38           # le dernier repos sert de marge de fin
        else:
            for rang in range(coups):
                _front_rebondissant(pulse, t, actif_pulse, nb_rebonds, duree_rebond, alea)
                t += fermeture * alea.uniform(0.95, 1.05)
                _front_rebondissant(pulse, t, 1 - actif_pulse, nb_rebonds, duree_rebond, alea)
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


def filtrer_hysteresis(signal: Signal, duree: float, confirm_actif: float,
                       confirm_repos: float, frequence: float) -> Signal:
    """Passe le signal dans le filtre du service et rend ce qu'il en sort.

    Rééchantillonne la trace à la cadence du thread d'échantillonnage, ce qui
    reproduit aussi l'effet de cette cadence : ce que rend cette fonction est
    littéralement ce que le service verra.
    """
    filtre = gpio_io.FiltreContact(confirm_actif, confirm_repos,
                                   signal.niveau_a(0.0) == signal.niveau_actif)
    propre = Signal(signal.nom, signal.pin, signal.niveau_actif, signal.libelle)
    propre.demarrer(signal.niveau_a(0.0))
    pas = 1.0 / frequence
    instant = 0.0
    while instant < duree:
        stable = filtre.echantillon(instant, signal.niveau_a(instant) == signal.niveau_actif)
        if stable is not None:
            # Daté de l'instant où le niveau est apparu, pas de celui où le
            # filtre l'a confirmé : sans cela les impulsions paraîtraient plus
            # longues et les repos plus courts qu'ils ne sont.
            propre.ajouter(filtre.debut_etat, signal.niveau_actif if stable
                           else 1 - signal.niveau_actif)
        instant += pas
    return propre


def signaux_filtres(trace: Trace, min_actif: float, min_repos: float,
                    frequence: float) -> Tuple[Signal, Signal]:
    """Les deux contacts du cadran vus à travers les filtres du service."""
    pulse = filtrer_hysteresis(trace.impulsions, trace.duree, min_actif, min_repos,
                               frequence)
    offnormal = filtrer_hysteresis(trace.offnormal, trace.duree,
                                   config.OFFNORMAL_CONFIRM_SEC,
                                   config.OFFNORMAL_CONFIRM_SEC, frequence)
    return pulse, offnormal


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


def mesurer(trace: Trace, min_actif: float, min_repos: float,
            frequence: float) -> Tuple[List[int], List[str]]:
    """Décrit le signal filtré : chiffres composés + remarques."""
    duree = trace.duree
    pulse, offnormal = signaux_filtres(trace, min_actif, min_repos, frequence)
    remarques: List[str] = []
    chiffres: List[int] = []

    fenetres = fenetres_cadran(offnormal, duree)
    if not fenetres:
        remarques.append("aucun mouvement du cadran détecté sur le contact off-normal")
        return chiffres, remarques

    for index, (debut, fin) in enumerate(fenetres, start=1):
        coups = [t for t in pulse.fronts_vers_actif() if debut <= t < fin]
        section(f"Rotation n°{index} — cadran hors repos pendant "
                f"{(fin - debut) * 1000:.0f} ms")
        if not coups:
            # Même règle que PhoneInputs : sans impulsion, ce n'est pas un
            # chiffre mais un rebond de l'off-normal, ou le cadran effleuré.
            info("aucune impulsion → aucun chiffre validé")
            remarques.append(f"rotation n°{index} : aucune impulsion dans la "
                             f"fenêtre du cadran ({(fin - debut) * 1000:.0f} ms)")
            continue
        chiffre = len(coups) % 10
        chiffres.append(chiffre)
        info(f"{len(coups)} impulsion(s) → chiffre {chiffre}")

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
                f"rotation n°{index} : seulement {marge_debut:.1f} ms entre le départ du "
                f"cadran et la 1re impulsion — si le callback off-normal arrive après "
                f"cette impulsion, elle est ignorée (chiffre trop petit d'une unité)")
        if marge_fin < 15:
            remarques.append(
                f"rotation n°{index} : seulement {marge_fin:.1f} ms entre la dernière "
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


# --- Verdict de câblage -------------------------------------------------

def excursions(signal: Signal, niveau: int, duree: float) -> List[Tuple[float, float]]:
    """Intervalles (début, durée) pendant lesquels le signal est à ce niveau."""
    plages = []
    for i, (instant, valeur) in enumerate(signal.transitions):
        if valeur != niveau:
            continue
        fin = (signal.transitions[i + 1][0]
               if i + 1 < len(signal.transitions) else duree)
        plages.append((instant, fin - instant))
    return plages


def niveau_au_repos(signal: Signal, duree: float) -> int:
    """Niveau que la broche tient le plus longtemps : son état au repos."""
    temps = {0: 0.0, 1: 0.0}
    for niveau in (0, 1):
        temps[niveau] = sum(d for _, d in excursions(signal, niveau, duree))
    return 0 if temps[0] >= temps[1] else 1


def diagnostiquer(trace: Trace, seuil_impulsion: float, min_actif: float,
                  min_repos: float, frequence: float) -> List[str]:
    """Ce que le signal dit du contact, avant et après le filtre du service.

    L'ordre compte : une broche qui ne porte rien ne se rattrape par aucun
    réglage, alors qu'un contact usé mais dont le repos reste franc se rattrape
    entièrement. Le verdict porte donc sur le signal **filtré**, le signal brut
    ne servant qu'à décrire l'état du contact.
    """
    alertes: List[str] = []
    duree = trace.duree
    pulse = trace.impulsions
    repos = niveau_au_repos(pulse, duree)
    haut = sum(d for _, d in excursions(pulse, 1, duree))

    section("Verdict du contact d'impulsions")
    info(f"niveau au repos : {'HAUT (3,3 V)' if repos else 'BAS (0 V)'} · "
         f"{haut / duree * 100:.1f} % du temps au niveau haut")
    info(f"niveau actif configuré : {config.PULSE_ACTIF_LEVEL} "
         f"{GRIS}[{harness.provenance('PULSE_ACTIF_LEVEL')}]{RAZ}")

    # Une impulsion est, par définition, un écart au niveau de repos : c'est
    # celui-là qu'on mesure, même si la configuration désigne l'autre — sinon
    # une polarité inversée ferait prendre les longues plages de repos pour
    # des impulsions de plusieurs secondes.
    niveau_impulsion = 1 - repos
    if repos == pulse.niveau_actif:
        inverse = "HIGH" if pulse.niveau_actif == 0 else "LOW"
        alerte(f"la broche est AU REPOS sur le niveau déclaré actif "
               f"({config.PULSE_ACTIF_LEVEL}) : le service croit donc voir une "
               f"impulsion permanente. Soit la polarité est inversée — essayez "
               f"PULSE_ACTIF_LEVEL={inverse} —, soit le contact est court-circuité "
               f"en permanence (mauvaise paire de fils, ou contact de shunt du "
               f"cadran câblé en parallèle).")
        alertes.append(f"broche d'impulsions au repos sur le niveau actif "
                       f"({config.PULSE_ACTIF_LEVEL})")

    brutes = excursions(pulse, niveau_impulsion, duree)
    info(f"signal brut : {len(pulse.fronts)} front(s), {len(brutes)} écart(s) au "
         f"niveau de repos")
    salves = pires_rebonds(pulse, 0.005)
    if salves:
        debut_salve, fin_salve, nombre = salves[0]
        longueur = (fin_salve - debut_salve) * 1000
        info(f"plus longue salve de grésillement : {longueur:.1f} ms "
             f"({nombre} fronts) {GRIS}— absorbée tant qu'elle reste sous "
             f"PULSE_MIN_REPOS_SEC = {min_repos * 1000:.0f} ms{RAZ}")

    # Le verdict porte sur ce que le filtre laisse passer. Si la polarité
    # configurée est fausse, on mesure quand même les vraies impulsions —
    # celles qui s'écartent du repos — sans quoi les longues plages de repos
    # passeraient pour des impulsions et masqueraient le diagnostic.
    a_mesurer = pulse
    if repos == pulse.niveau_actif:
        a_mesurer = Signal(pulse.nom, pulse.pin, niveau_impulsion, pulse.libelle)
        a_mesurer.transitions = list(pulse.transitions)
    propre = filtrer_hysteresis(a_mesurer, duree, min_actif, min_repos, frequence)
    impulsions = excursions(propre, propre.niveau_actif, duree)
    plausibles = [(t, d) for t, d in impulsions if d >= seuil_impulsion]
    info(f"après filtrage ({min_actif * 1000:.0f} ms actif / "
         f"{min_repos * 1000:.0f} ms repos, {frequence / 1000:.1f} kHz) : "
         f"{len(impulsions)} impulsion(s), dont {len(plausibles)} d'au moins "
         f"{seuil_impulsion * 1000:.0f} ms")
    retenues = sorted(d for _, d in (plausibles or impulsions))
    if retenues:
        milieu = retenues[len(retenues) // 2]
        info(f"durées : {retenues[0] * 1000:.1f} → {retenues[-1] * 1000:.1f} ms "
             f"(médiane {milieu * 1000:.1f}) {GRIS}— attendu ~33 ms à "
             f"10 impulsions/s{RAZ}")

    if not plausibles:
        alerte("AUCUNE impulsion d'une durée plausible ne sort du filtre : rien "
               "de ce qui arrive sur cette broche ne ressemble au train "
               "d'impulsions d'un cadran (10 créneaux de ~33 ms étalés sur ~1 s). "
               "C'est un problème de câblage ou de contact, qu'aucun réglage "
               "logiciel ne corrigera.")
        alertes.append("aucune impulsion plausible, même après filtrage")
        return alertes

    # Repos les plus courts entre deux impulsions filtrées : c'est eux qui
    # bornent PULSE_MIN_REPOS_SEC par le haut.
    creux = sorted(d for _, d in excursions(propre, 1 - propre.niveau_actif, duree)
                   if 0 < d < 0.5)
    if creux:
        info(f"plus court repos entre deux impulsions : {creux[0] * 1000:.1f} ms "
             f"{GRIS}— PULSE_MIN_REPOS_SEC doit rester en dessous{RAZ}")
        if min_repos >= creux[0]:
            alerte(f"PULSE_MIN_REPOS_SEC ({min_repos * 1000:.0f} ms) dépasse le plus "
                   f"court repos mesuré ({creux[0] * 1000:.1f} ms) : deux impulsions "
                   f"voisines finiront par n'en faire qu'une.")
            alertes.append("repos exigé plus long que le repos réel du cadran")

    if len(plausibles) >= 2:
        intervalles = sorted(plausibles[i + 1][0] - plausibles[i][0]
                             for i in range(len(plausibles) - 1)
                             if plausibles[i + 1][0] - plausibles[i][0] < 0.5)
        periode = intervalles[len(intervalles) // 2] if intervalles else 0.0
        cadence = 1 / periode if periode else 0
        info(f"cadence : {cadence:.1f} impulsions/s {GRIS}— attendu 8 à 12{RAZ}")
        if intervalles and not 6 <= cadence <= 16:
            alerte(f"cadence hors de tout cadran normalisé ({cadence:.1f}/s) : le "
                   f"filtre laisse encore passer des rebonds, ou en avale de "
                   f"vraies impulsions. Voyez le balayage ci-dessous.")
            alertes.append(f"cadence aberrante après filtrage ({cadence:.1f}/s)")
        elif intervalles and len(pulse.fronts) > 4 * len(impulsions):
            ok(f"contact usé ({len(pulse.fronts)} fronts bruts pour "
               f"{len(impulsions)} impulsions) mais entièrement rattrapé par le "
               f"filtre : le repos entre impulsions est franc.")

    salve_off = pires_rebonds(trace.offnormal, 0.005)
    if salve_off:
        debut_salve, fin_salve, nombre = salve_off[0]
        longueur = (fin_salve - debut_salve) * 1000
        info(f"off-normal : plus longue salve {longueur:.1f} ms ({nombre} fronts) · "
             f"OFFNORMAL_CONFIRM_SEC = {config.OFFNORMAL_CONFIRM_SEC} s")
        if longueur > config.OFFNORMAL_CONFIRM_SEC * 1000:
            alerte(f"off-normal : la salve de rebonds ({longueur:.1f} ms) dépasse la "
                   f"confirmation ({config.OFFNORMAL_CONFIRM_SEC * 1000:.0f} ms) — "
                   f"essayez OFFNORMAL_CONFIRM_SEC={max(longueur * 1.5, 10) / 1000:.3f}")
            alertes.append("off-normal : confirmation plus courte que les rebonds")
    return alertes


def surveiller_niveaux(periode: float) -> None:
    """Affiche en continu le niveau des trois broches et leur taux d'occupation.

    C'est l'outil du test qui tranche : débranchez le fil du contact
    d'impulsions ; la broche doit remonter à 3,3 V (niveau HAUT, 100 %) grâce
    au pull-up interne. Si elle reste au niveau BAS débranchée, le problème
    est côté Raspberry Pi (mauvaise broche, court-circuit à la masse) ; si
    elle remonte, le pull-up marche et c'est bien le contact du cadran qui la
    tient à la masse en permanence.
    """
    GPIO = gpio_io.GPIO
    if GPIO is None:
        print(f"\n{JAUNE}RPi.GPIO indisponible : --niveaux doit être lancé sur le "
              f"Raspberry Pi câblé.{RAZ}\n")
        raise SystemExit(2)

    broches = [("impulsions", config.DIAL_PULSE_PIN, gpio_io.PULSE_ACTIVE_LEVEL),
               ("off-normal", config.DIAL_OFFNORMAL_PIN, gpio_io.DIAL_ACTIVE_LEVEL),
               ("crochet", config.HOOK_PIN, gpio_io.HOOK_ACTIVE_LEVEL)]
    GPIO.setmode(GPIO.BCM)
    for _, pin, _ in broches:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    section("Moniteur de niveaux (Ctrl+C pour arrêter)")
    info("Test décisif : débranchez le fil du contact d'impulsions — la broche "
         "doit passer à HAUT 100 % (pull-up interne).")
    print()
    try:
        while True:
            hauts = {nom: 0 for nom, _, _ in broches}
            total = 0
            fin = time.perf_counter() + periode
            while time.perf_counter() < fin:
                for nom, pin, _ in broches:
                    hauts[nom] += GPIO.input(pin)
                total += 1
                time.sleep(0.001)
            cellules = []
            for nom, pin, actif in broches:
                niveau = GPIO.input(pin)
                part = hauts[nom] / total * 100 if total else 0.0
                etat = "HAUT" if niveau else "BAS "
                couleur = ROUGE if niveau == actif else VERT
                cellules.append(f"{nom} (GPIO {pin}) {couleur}{etat}{RAZ} "
                                f"{GRIS}{part:5.1f} % haut{RAZ}")
            print("  " + " · ".join(cellules))
    except KeyboardInterrupt:
        print(f"\n  {GRIS}arrêt du moniteur{RAZ}\n")
    finally:
        GPIO.cleanup()


# --- Rejeu dans la vraie machine à états --------------------------------

def rejouer(trace: Trace, min_actif: float, min_repos: float,
            frequence: float) -> List[int]:
    """Décode la trace exactement comme le service le ferait.

    Mêmes filtres, même cadence d'échantillonnage, même ordre des contacts,
    même PhoneInputs : ce n'est pas un modèle du décodage, c'est le décodage.
    """
    inputs = gpio_io.PhoneInputs()
    inputs.set_hook(True)
    pulse = trace.impulsions
    offnormal = trace.offnormal

    filtres = [
        # L'off-normal est traité avant les impulsions, comme dans la boucle du
        # service : à égalité d'instant, la rotation s'ouvre avant qu'on y
        # compte une impulsion.
        ("off-normal", offnormal,
         gpio_io.FiltreContact(config.OFFNORMAL_CONFIRM_SEC,
                               config.OFFNORMAL_CONFIRM_SEC,
                               offnormal.niveau_a(0.0) == offnormal.niveau_actif)),
        ("impulsions", pulse,
         gpio_io.FiltreContact(min_actif, min_repos,
                               pulse.niveau_a(0.0) == pulse.niveau_actif)),
    ]
    inputs.set_dial_active(offnormal.niveau_a(0.0) == offnormal.niveau_actif)

    chiffres: List[int] = []
    pas = 1.0 / frequence
    instant = 0.0
    while instant < trace.duree:
        for nom, signal, filtre in filtres:
            stable = filtre.echantillon(instant,
                                        signal.niveau_a(instant) == signal.niveau_actif)
            if stable is not None:
                gpio_io.appliquer(inputs, nom, stable)
        instant += pas
    chiffre = inputs.pop_digit()
    while chiffre is not None:
        chiffres.append(chiffre)
        chiffre = inputs.pop_digit()
    return chiffres


def compter_impulsions(trace: Trace, min_actif: float, min_repos: float,
                       frequence: float) -> int:
    """Nombre d'impulsions que le filtre retient, toutes rotations confondues."""
    pulse, _ = signaux_filtres(trace, min_actif, min_repos, frequence)
    return len(pulse.fronts_vers_actif())


def balayer(trace: Trace, attendu: List[int], min_actif: float,
            frequence: float) -> None:
    """Balaie PULSE_MIN_REPOS_SEC et affiche le palier où le décodage est juste.

    C'est le tableau qui donne le réglage : une valeur isolée qui tombe juste
    ne vaut rien, seul un palier large garantit que le prochain appel sera lu
    pareil. On croise avec la cadence d'échantillonnage pour vérifier que le
    résultat n'en dépend pas.
    """
    valeurs = [2, 5, 8, 10, 12, 15, 20, 25, 30, 35, 40, 50, 60]
    frequences = [500.0, frequence, 2000.0] if frequence not in (500.0, 2000.0) \
        else [500.0, 1000.0, 2000.0]
    actuel = int(round(config.PULSE_MIN_REPOS_SEC * 1000))
    reference = "".join(str(c) for c in attendu)

    section("Balayage du repos exigé (PULSE_MIN_REPOS_SEC)")
    info(f"référence : {reference or '—'}")
    entete = f"  {'repos exigé':>12} │ " + " │ ".join(
        f"{f / 1000:.1f} kHz".rjust(14) for f in frequences)
    print(GRIS + entete + RAZ)
    bons: List[int] = []
    for ms in valeurs:
        cellules = []
        for f in frequences:
            lus = "".join(str(c) for c in rejouer(trace, min_actif, ms / 1000.0, f))
            juste = lus == reference
            if juste and f == frequence:
                bons.append(ms)
            couleur = VERT if juste else ROUGE
            cellules.append(f"{couleur}{(lus or '—'):>14}{RAZ}")
        marque = f" {GRAS}<< config actuelle{RAZ}" if ms == actuel else ""
        print(f"  {ms:>9} ms │ " + " │ ".join(cellules) + marque)

    print()
    if not bons:
        alerte("aucune durée de repos ne décode le bon numéro : le signal ne porte "
               "pas le nombre d'impulsions attendu. Reprenez le verdict de câblage "
               "et les marges off-normal ci-dessus.")
        return

    # Le palier est la plus longue plage contiguë de valeurs justes : c'est en
    # son centre que le réglage supporte le mieux l'usure du contact.
    paliers, courant = [], [bons[0]]
    for precedent, valeur in zip(bons, bons[1:]):
        if valeurs.index(valeur) == valeurs.index(precedent) + 1:
            courant.append(valeur)
        else:
            paliers.append(courant)
            courant = [valeur]
    paliers.append(courant)
    palier = max(paliers, key=len)
    centre = (palier[0] + palier[-1]) / 2

    if len(palier) < 2:
        note(f"seule la valeur {palier[0]} ms décode juste : palier trop étroit "
             f"pour être sûr. Recommencez la capture, et nettoyez le contact.")
    else:
        ok(f"palier de {palier[0]} à {palier[-1]} ms — réglez au centre : "
           f"PULSE_MIN_REPOS_SEC={centre / 1000:.3f}")
    if actuel not in bons:
        alerte(f"PULSE_MIN_REPOS_SEC = {config.PULSE_MIN_REPOS_SEC} s décode faux "
               f"sur cette trace (page /settings du dashboard, ou variable "
               f"d'environnement).")


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
    parseur.add_argument("--min-actif-ms", type=float, dest="min_actif_ms",
                         default=config.PULSE_MIN_ACTIF_SEC * 1000,
                         help="Niveau actif franc qui ouvre une impulsion "
                              "(PULSE_MIN_ACTIF_SEC).")
    parseur.add_argument("--min-repos-ms", type=float, dest="min_repos_ms",
                         default=config.PULSE_MIN_REPOS_SEC * 1000,
                         help="Repos franc qui clôt l'impulsion (PULSE_MIN_REPOS_SEC). "
                              "C'est le réglage décisif sur un contact usé.")
    parseur.add_argument("--glitch-ms", type=float, default=5.0, dest="glitch_ms",
                         help="Diagramme seulement : sous cette durée, un état est "
                              "écarté du tracé (défaut 5 ms).")
    parseur.add_argument("--niveaux", action="store_true",
                         help="Moniteur de niveaux en continu au lieu d'une capture : "
                              "l'outil du test de câblage (débrancher le fil "
                              "d'impulsions et voir si la broche remonte à 3,3 V).")
    parseur.add_argument("--seuil-impulsion-ms", type=float, default=10.0,
                         dest="seuil_impulsion_ms",
                         help="Durée minimale d'une excursion pour être tenue pour "
                              "une vraie impulsion (défaut 10 ms ; une impulsion de "
                              "cadran en dure ~33).")
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
    parseur.add_argument("--contact-use", action="store_true", dest="contact_use",
                         help="Simulation : contact d'impulsions usé, qui grésille "
                              "pendant toute la fermeture (le cas réel que le "
                              "filtre doit savoir lire).")
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
                "OFFNORMAL_ACTIF_LEVEL", "PULSE_ACTIF_LEVEL",
                "PULSE_MIN_ACTIF_SEC", "PULSE_MIN_REPOS_SEC",
                "OFFNORMAL_CONFIRM_SEC", "GPIO_ECHANTILLONNAGE_HZ"):
        info(f"{nom} = {getattr(config, nom)!r}   {GRIS}[{harness.provenance(nom)}]{RAZ}")

    if args.niveaux:
        surveiller_niveaux(0.5)
        return

    if args.reel:
        trace = capturer_reel(args.duree, args.freq, avec_crochet=True)
    else:
        numero_simule = args.numero or "19"
        trace = capturer_simule(numero_simule, args.rebonds, args.rebond_ms / 1000.0,
                                args.imp_par_sec, args.marge_fin_ms / 1000.0,
                                args.graine, args.contact_use)
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

    min_actif = args.min_actif_ms / 1000.0
    min_repos = args.min_repos_ms / 1000.0
    frequence = config.GPIO_ECHANTILLONNAGE_HZ
    alertes_cablage = diagnostiquer(trace, args.seuil_impulsion_ms / 1000.0,
                                    min_actif, min_repos, frequence)

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

    section(f"Mesures sur le signal filtré ({args.min_actif_ms:.0f} ms actif / "
            f"{args.min_repos_ms:.0f} ms repos)")
    chiffres, remarques = mesurer(trace, min_actif, min_repos, frequence)

    section("Ce que le service aurait lu (rejeu dans gpio_io.PhoneInputs)")
    declare = [int(c) for c in (args.numero or "") if c.isdigit()]
    if not args.reel and not declare:
        declare = [int(c) for c in numero_simule if c.isdigit()]
    # La référence est le numéro annoncé quand on le connaît ; sinon celui que
    # le signal filtré permet de reconstruire.
    reference = declare or chiffres
    mesure = "".join(str(c) for c in chiffres)
    attendu = "".join(str(c) for c in reference)
    lus = "".join(str(c) for c in rejouer(trace, min_actif, min_repos, frequence))
    if declare:
        info(f"numéro composé (annoncé)  : {GRAS}{attendu or '—'}{RAZ}")
    info(f"reconstruit du signal filtré : {GRAS}{mesure or '—'}{RAZ}")
    info(f"décodé par le service (échantillonné à {frequence / 1000:.1f} kHz) : "
         f"{GRAS}{lus or '—'}{RAZ}")
    if declare and mesure != attendu:
        alerte(f"le signal filtré ne redonne pas le bon numéro avec les réglages "
               f"actuels : lisez le palier du balayage ci-dessous avant de "
               f"conclure. Si aucune valeur ne convient, le contact est à "
               f"nettoyer — aucun réglage ne rattrapera ce signal.")
    if lus == attendu and attendu:
        ok("le décodage est juste sur cette trace")
    else:
        alerte("le décodage diffère du numéro composé : c'est exactement le "
               "symptôme observé sur le téléphone")

    if reference:
        balayer(trace, reference, min_actif, frequence)

    if alertes_cablage or remarques:
        section("Pistes")
        if alertes_cablage:
            info(f"{GRAS}Le câblage d'abord — tant que la broche ne délivre pas un "
                 f"vrai train d'impulsions, aucun réglage ne donnera le bon "
                 f"chiffre :{RAZ}")
            for remarque in alertes_cablage:
                alerte(remarque)
            info("Test décisif : débranchez le fil du contact d'impulsions et "
                 "lancez `python3 tests/scope_impulsions.py --niveaux` — la broche "
                 "doit remonter à HAUT 100 %.")
        else:
            info(f"{GRAS}Le contact est usé mais lisible : c'est le réglage du "
                 f"filtre qui décide, voyez le palier du balayage.{RAZ}")
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
