#!/usr/bin/env python3
"""Test unitaire de la COMPOSITION D'UN NUMÉRO au cadran rotatif (§1.2, §5.1, §5.7).

Un cadran rotatif ne transmet pas de chiffre : il ouvre un contact
« off-normal » pendant toute sa rotation et émet N impulsions, N valant le
chiffre composé — sauf le 0, qui en vaut dix. Le chiffre n'est validé qu'au
retour du cadran au repos.

Ce que vérifie ce script, seul et sans matériel :

1. le décodage impulsions -> chiffre, y compris le cas du 0 ;
2. les impulsions parasites hors rotation sont ignorées ;
3. plusieurs chiffres s'enchaînent dans l'ordre (file FIFO) ;
4. la tonalité est coupée dès la première impulsion ;
5. le chiffre composé sélectionne le bon message, avec repli sur le message
   générique si le chiffre n'a pas de message dédié ;
6. en mode restitution, la saisie multi-chiffres, sa validation par silence
   du cadran et le bornage du numéro (§5.7) ;
7. la tonalité d'invitation à numéroter, tenue tant que rien n'est parti et
   coupée dès la première impulsion (§1.2).

Usage :
    python3 tests/test_composition.py          # simulation, aucune dépendance
    python3 tests/test_composition.py --reel   # sur le Raspberry Pi câblé
"""

import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import config                        # noqa: E402
import gpio_io                       # noqa: E402
import livre_dor                     # noqa: E402


def _composer(inputs: gpio_io.PhoneInputs, chiffre: int) -> None:
    """Rotation complète du cadran pour un chiffre (0 = 10 impulsions)."""
    inputs.set_dial_active(True)
    for _ in range(10 if chiffre == 0 else chiffre):
        inputs.register_pulse()
    inputs.set_dial_active(False)


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Décodage impulsions -> chiffre (gpio_io.PhoneInputs)")
    inputs = gpio_io.PhoneInputs()
    rapport.verifie("aucun chiffre en attente au départ", inputs.pop_digit() is None)
    for chiffre in range(1, 10):
        _composer(inputs, chiffre)
        obtenu = inputs.pop_digit()
        rapport.egal(f"{chiffre} impulsion(s) donnent le chiffre {chiffre}",
                     obtenu, chiffre)
    _composer(inputs, 0)
    rapport.egal("10 impulsions donnent le chiffre 0", inputs.pop_digit(), 0)

    rapport.section("2. Robustesse du comptage")
    inputs = gpio_io.PhoneInputs()
    inputs.register_pulse()
    inputs.register_pulse()
    rapport.verifie("les impulsions hors rotation sont ignorées",
                    inputs.has_pulses() is False)
    rapport.verifie("aucun chiffre n'est produit", inputs.pop_digit() is None)

    inputs.set_dial_active(True)
    for _ in range(11):
        inputs.register_pulse()
    inputs.set_dial_active(False)
    rapport.egal("11 impulsions (rebond) retombent sur 1", inputs.pop_digit(), 1)

    # Le contact off-normal rebondit jusqu'à 100 ms au retour au repos : chaque
    # rebond ouvrait puis refermait une rotation sans impulsion, qui validait
    # un « 0 » (0 % 10) au milieu du numéro composé.
    inputs.set_dial_active(True)
    inputs.set_dial_active(False)
    rapport.verifie("un aller-retour sans impulsion ne valide aucun chiffre",
                    inputs.pop_digit() is None)
    for _ in range(5):
        inputs.set_dial_active(True)
        inputs.set_dial_active(False)
    rapport.verifie("une salve de rebonds de l'off-normal n'insère aucun 0",
                    inputs.pop_digit() is None)
    inputs.set_dial_active(True)
    inputs.register_pulse()
    inputs.set_dial_active(False)
    rapport.egal("une vraie impulsion reste comptée après les rebonds",
                 inputs.pop_digit(), 1)

    inputs.set_dial_active(True)
    inputs.register_pulse()
    rapport.verifie("has_pulses() est vrai dès la 1re impulsion",
                    inputs.has_pulses() is True)
    rapport.verifie("is_dial_active() est vrai pendant toute la rotation",
                    inputs.is_dial_active() is True)
    inputs.set_dial_active(False)
    rapport.verifie("is_dial_active() retombe au repos du cadran",
                    inputs.is_dial_active() is False)
    rapport.verifie("le compteur est remis à zéro après validation",
                    inputs.has_pulses() is False)

    rapport.section("3. Lecture d'un contact d'impulsions usé (gpio_io.FiltreContact)")
    # Signal calqué sur une capture réelle : le contact grésille pendant toute
    # la fermeture, mais le repos entre deux impulsions reste franc. C'est cette
    # dissymétrie que le filtre exploite, et qu'un anti-rebond à fenêtre unique
    # ne sait pas exprimer.
    segments = harness.segments_cadran_use(6)
    echantillons = harness.echantillonner(segments, config.GPIO_ECHANTILLONNAGE_HZ)
    fronts_bruts = sum(1 for precedent, suivant in zip(segments, segments[1:])
                       if precedent[1] != suivant[1])
    rapport.info(f"{fronts_bruts} fronts bruts pour 6 impulsions réelles")

    def _compter(min_actif: float, min_repos: float) -> int:
        filtre = gpio_io.FiltreContact(min_actif, min_repos)
        return sum(1 for instant, actif in echantillons
                   if filtre.echantillon(instant, actif) is True)

    rapport.egal("le grésillement est absorbé : 6 impulsions comptées",
                 _compter(config.PULSE_MIN_ACTIF_SEC, config.PULSE_MIN_REPOS_SEC), 6)
    rapport.egal("un repos exigé trop court compte une impulsion de trop",
                 _compter(config.PULSE_MIN_ACTIF_SEC, 0.005), 7)
    rapport.egal("un repos exigé plus long que le repos réel en soude deux",
                 _compter(config.PULSE_MIN_ACTIF_SEC, 0.060) < 6, True)

    palier = [ms for ms in range(11, 36)
              if _compter(config.PULSE_MIN_ACTIF_SEC, ms / 1000.0) == 6]
    rapport.verifie("le réglage tient sur un palier large, pas sur une valeur",
                    len(palier) == 25,
                    f"valeurs justes entre 11 et 35 ms : {palier}")
    rapport.verifie("le défaut PULSE_MIN_REPOS_SEC tombe dans ce palier",
                    int(config.PULSE_MIN_REPOS_SEC * 1000) in palier,
                    f"défaut {config.PULSE_MIN_REPOS_SEC} s, palier {palier[0]}-{palier[-1]} ms")

    # Un contact sain ne doit rien perdre au passage du filtre.
    sain = harness.segments_cadran_use(7, coupure_longue=0.0)
    sain = [(duree, actif) for duree, actif in sain]
    filtre = gpio_io.FiltreContact(config.PULSE_MIN_ACTIF_SEC, config.PULSE_MIN_REPOS_SEC)
    comptees = sum(1 for instant, actif in harness.echantillonner(
        sain, config.GPIO_ECHANTILLONNAGE_HZ)
        if filtre.echantillon(instant, actif) is True)
    rapport.egal("un cadran sans coupure parasite donne son compte exact",
                 comptees, 7)

    # La boucle du service n'échantillonne à pleine cadence que pendant
    # GPIO_ACTIVITE_SEC après le dernier front : le reste du temps elle somnole.
    # Le cas à éprouver est donc celui où le cadran se met à tourner alors
    # qu'elle est au repos — c'est ainsi que commence toute composition.
    adaptatif = harness.echantillonner_adaptatif(
        segments, config.GPIO_ECHANTILLONNAGE_REPOS_HZ,
        config.GPIO_ECHANTILLONNAGE_HZ, config.GPIO_ACTIVITE_SEC)
    filtre = gpio_io.FiltreContact(config.PULSE_MIN_ACTIF_SEC, config.PULSE_MIN_REPOS_SEC)
    comptees = sum(1 for instant, actif in adaptatif
                   if filtre.echantillon(instant, actif) is True)
    rapport.egal("la cadence adaptative ne coûte aucune impulsion", comptees, 6)
    rapport.verifie("et elle prélève moins d'échantillons qu'une cadence fixe",
                    len(adaptatif) < len(echantillons),
                    f"adaptatif {len(adaptatif)}, fixe {len(echantillons)}")

    rapport.section("4. États filtrés du crochet et du cadran")
    # Crochet et off-normal sont des états : une confirmation symétrique suffit,
    # mais elle doit laisser passer un décroché franc sans le retarder à l'excès.
    filtre = gpio_io.FiltreContact(config.HOOK_CONFIRM_SEC, config.HOOK_CONFIRM_SEC)
    decroche = harness.echantillonner([(0.05, False), (0.5, True)],
                                      config.GPIO_ECHANTILLONNAGE_HZ)
    bascules = [instant for instant, actif in decroche
                if filtre.echantillon(instant, actif) is True]
    rapport.verifie("un décroché franc est vu une fois et une seule",
                    len(bascules) == 1, f"bascules : {bascules}")
    rapport.verifie("il est vu après la confirmation, pas avant",
                    bascules and bascules[0] >= 0.05 + config.HOOK_CONFIRM_SEC,
                    f"vu à {bascules[0]:.3f} s, confirmation {config.HOOK_CONFIRM_SEC} s")

    filtre = gpio_io.FiltreContact(config.OFFNORMAL_CONFIRM_SEC,
                                   config.OFFNORMAL_CONFIRM_SEC)
    salve = harness.echantillonner(
        [(0.2, False)] + [(0.003, True), (0.003, False)] * 8 + [(0.2, False)],
        config.GPIO_ECHANTILLONNAGE_HZ)
    rapport.verifie("une salve de rebonds de l'off-normal n'ouvre aucune rotation",
                    all(filtre.echantillon(instant, actif) is None
                        for instant, actif in salve))

    rapport.section("5. File de chiffres et purge")
    inputs = gpio_io.PhoneInputs()
    for chiffre in (4, 2, 0):
        _composer(inputs, chiffre)
    rapport.egal("les chiffres sortent dans l'ordre composé",
                 [inputs.pop_digit(), inputs.pop_digit(), inputs.pop_digit()],
                 [4, 2, 0])
    rapport.verifie("la file est vide ensuite", inputs.pop_digit() is None)

    for chiffre in (7, 7):
        _composer(inputs, chiffre)
    inputs.set_dial_active(True)
    inputs.register_pulse()
    inputs.reset_dial()
    rapport.verifie("reset_dial() purge les chiffres en attente",
                    inputs.pop_digit() is None)
    rapport.verifie("reset_dial() purge les impulsions en cours",
                    inputs.has_pulses() is False)
    rapport.verifie("reset_dial() remet le cadran au repos",
                    inputs.is_dial_active() is False)

    rapport.section("4. La tonalité est coupée dès la 1re impulsion (§1.2)")
    with Banc() as banc:
        banc.decrocher(stabiliser=False)
        rapport.verifie("la tonalité démarre", banc.attendre_lecture("tonalite.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.inputs.set_dial_active(True)
        banc.inputs.register_pulse()
        time.sleep(0.1)
        rapport.verifie("la tonalité est interrompue",
                        "tonalite.wav" in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        # Fin de la rotation : le chiffre 3 est validé au retour au repos.
        for _ in range(2):
            banc.inputs.register_pulse()
        banc.inputs.set_dial_active(False)
        rapport.verifie("le chiffre composé est pris en compte",
                        banc.attendre_lecture("message_3.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("5. Chiffre composé -> message correspondant")
    with harness.journal_status() as journal, Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(5)
        rapport.verifie("le chiffre 5 joue message_5.wav",
                        banc.attendre_lecture("message_5.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("status.json nomme le message lu (§5.2)",
                        (livre_dor.STATE_LECTURE_MESSAGE, "message_5.wav") in journal,
                        f"états publiés : {journal}")

    with Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(0)
        rapport.verifie("le chiffre 0 joue message_0.wav",
                        banc.attendre_lecture("message_0.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("6. Repli sur le message générique (§1.2)")
    with Banc(chiffres_maries=[1, 2]) as banc:
        rapport.egal("un chiffre pourvu garde son message",
                     livre_dor.message_path_for_digit(2).name, "message_2.wav")
        rapport.egal("un chiffre sans message retombe sur le générique",
                     livre_dor.message_path_for_digit(8).name, "message_generique.wav")
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(8)
        rapport.verifie("le message générique est joué",
                        banc.attendre_lecture("message_generique.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("7. Numéro multi-chiffres en mode restitution (§5.7)")
    with Banc(restitution=True, messages=20, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(1, 2)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("« 12 » lit le 12e message chronologique",
                     banc.messages_invites_lus(), [banc.noms_messages[11]])

    with Banc(restitution=True, messages=20, RESTITUTION_INTERDIGIT_SEC=0.4,
              RESTITUTION_DIGITS_MAX=4) as banc:
        banc.decrocher()
        banc.composer(3, 6, 9, 5)
        time.sleep(3 * harness.STABILISATION_SEC)   # sans attendre l'inter-chiffre
        rapport.egal("le 4e chiffre ferme la saisie sans attendre l'inter-chiffre",
                     banc.messages_invites_lus(), [banc.noms_messages[-1]])
        banc.composer(7)
        time.sleep(3 * harness.STABILISATION_SEC)
        rapport.egal("un 5e chiffre reste sans effet",
                     banc.messages_invites_lus(), [banc.noms_messages[-1]])

    rapport.section("8. L'inter-chiffre est suspendu pendant la rotation (§5.7)")
    # Sans le garde is_dial_active(), le numéro « 12 » serait validé à « 1 »
    # alors que le cadran est encore en train de composer le 2 (il met ~1 s à
    # revenir au repos).
    with Banc(restitution=True, messages=20, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(1)
        banc.inputs.set_dial_active(True)           # rotation lente du 2e chiffre
        time.sleep(0.7)                             # > RESTITUTION_INTERDIGIT_SEC
        for _ in range(2):
            banc.inputs.register_pulse()
        banc.inputs.set_dial_active(False)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("le numéro attend la fin de la rotation : « 12 », pas « 1 »",
                     banc.messages_invites_lus(), [banc.noms_messages[11]])

    rapport.section("9. Bornage du numéro composé (§5.7)")
    with Banc(restitution=True, messages=5, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(9)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("un numéro au-delà du dernier message lit le dernier",
                     banc.messages_invites_lus(), [banc.noms_messages[-1]])

    with Banc(restitution=True, messages=5, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(0)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("le numéro 0 lit le premier message",
                     banc.messages_invites_lus(), [banc.noms_messages[0]])
    rapport.section("10. La tonalité d'attente tient jusqu'à la 1re impulsion (§1.2)")
    # Sur une ligne PTT, la tonalité arrivait au décroché et restait là tant
    # qu'on ne composait rien. Le fichier, lui, a une fin : il est donc rejoué
    # jusqu'à la première coupure de boucle.
    with Banc() as banc:
        banc.decrocher(stabiliser=False)
        rapport.verifie("elle démarre dès le décroché",
                        banc.attendre_lecture("tonalite.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("elle est retenue tant que rien n'est composé",
                        banc.audio.attendre_nb_lectures("tonalite.wav", 3),
                        f"lectures de la tonalité : "
                        f"{len(banc.audio.lectures_de('tonalite.wav'))}")
        rapport.egal("et la ligne ne fait rien entendre d'autre",
                     sorted(set(banc.audio.noms_lus())), ["tonalite.wav"])

    with Banc(restitution=True, messages=20, RESTITUTION_INTERDIGIT_SEC=0.6) as banc:
        banc.decrocher(stabiliser=False)
        banc.attendre_lecture("tonalite.wav")
        banc.composer(1)
        time.sleep(0.15)
        tonalites = len(banc.audio.lectures_de("tonalite.wav"))
        time.sleep(0.3)
        banc.composer(2)
        # Le compteur d'impulsions du chiffre retombe à zéro au retour du
        # cadran au repos : sans drapeau collant, la tonalité repartirait
        # dans ce creux, au beau milieu du numéro.
        rapport.egal("elle ne repart pas entre deux chiffres d'un même numéro",
                     len(banc.audio.lectures_de("tonalite.wav")), tonalites)
        time.sleep(0.6 + 3 * harness.STABILISATION_SEC)
        rapport.egal("et « 12 » lit bien le 12e message",
                     banc.messages_invites_lus(), [banc.noms_messages[11]])


def test_reel(rapport: Rapport) -> None:
    """Vérification du câblage réel du cadran (§7.6 point 3)."""
    rapport.section("Matériel : cadran rotatif")
    inputs = harness.exiger_gpio(rapport)
    try:
        rapport.verifie("le cadran est au repos au démarrage",
                        inputs.is_dial_active() is False,
                        "le contact off-normal est vu actif alors que le cadran "
                        f"est au repos : inversez OFFNORMAL_ACTIF_LEVEL "
                        f"(actuellement {config.OFFNORMAL_ACTIF_LEVEL})")

        for attendu in (3, 0):
            inputs.reset_dial()
            delai = harness.attendre_condition(
                lambda: inputs.pop_digit() is not None or inputs.has_pulses(),
                f"Composez le {attendu} au cadran.", timeout=30.0)
            if delai is None:
                rapport.verifie(f"le chiffre {attendu} est détecté", False,
                                "aucune impulsion reçue : vérifiez le câblage du "
                                f"cadran (off-normal GPIO {config.DIAL_OFFNORMAL_PIN}, "
                                f"impulsions GPIO {config.DIAL_PULSE_PIN})")
                continue
            # Laisser le cadran revenir complètement au repos avant de lire.
            time.sleep(1.5)
            obtenu = inputs.pop_digit()
            rapport.egal(f"le chiffre {attendu} est décodé correctement",
                         obtenu, attendu)
            if obtenu is not None and obtenu != attendu:
                rapport.info("un chiffre trop grand vient d'impulsions parasites "
                             f"(diminuez DIAL_DEBOUNCE_SEC={config.DIAL_DEBOUNCE_SEC}s "
                             "si des impulsions sont perdues, augmentez-le si elles "
                             "sont comptées en double)")
    finally:
        gpio_io.cleanup()


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("COMPOSITION D'UN NUMÉRO", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport)
    else:
        test_simule(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
