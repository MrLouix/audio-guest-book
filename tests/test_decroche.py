#!/usr/bin/env python3
"""Test unitaire de la fonction DÉCROCHÉ (§1.2, §5.1).

Ce que vérifie ce script, seul et sans matériel :

1. le contact du crochet est bien interprété par gpio_io.PhoneInputs ;
2. un décroché en mode mariage ouvre la tonalité puis la numérotation ;
3. le cadran est purgé au décroché (aucun chiffre résiduel du dernier appel) ;
4. un décroché pendant/juste après une sonnerie donne un « appel entrant »
   (message tiré au hasard, sans tonalité) ;
5. un décroché en mode restitution ouvre le parcours de relecture (§5.7) ;
6. l'état « décroché » est publié dans status.json pour le dashboard ;
7. la traduction du niveau logique (HOOK_ACTIVE_STATE) est correcte.

Usage :
    python3 tests/test_decroche.py            # simulation, aucune dépendance
    python3 tests/test_decroche.py --reel     # sur le Raspberry Pi câblé
"""

import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import config                        # noqa: E402
import gpio_io                       # noqa: E402
import livre_dor                     # noqa: E402


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Contact du crochet (gpio_io.PhoneInputs)")
    inputs = gpio_io.PhoneInputs()
    rapport.verifie("au repos, le combiné est raccroché", inputs.is_hook_up() is False)
    inputs.set_hook(True)
    rapport.verifie("après un décroché, is_hook_up() est vrai", inputs.is_hook_up() is True)
    inputs.set_hook(True)
    rapport.verifie("un second décroché ne change rien (idempotent)",
                    inputs.is_hook_up() is True)

    rapport.section("2. Niveau logique configuré (HOOK_ACTIVE_STATE)")
    rapport.egal("« LOW » vaut le niveau 0", gpio_io._active_level("LOW"), 0)
    rapport.egal("« HIGH » vaut le niveau 1", gpio_io._active_level("HIGH"), 1)
    attendu = 0 if config.HOOK_ACTIVE_STATE.upper() == "LOW" else 1
    rapport.egal(f"le niveau actif du crochet suit la config ({config.HOOK_ACTIVE_STATE})",
                 gpio_io.HOOK_ACTIVE_LEVEL, attendu)
    try:
        gpio_io._active_level("PEUT-ETRE")
        rapport.verifie("un niveau inconnu est refusé", False, "aucune exception levée")
    except ValueError:
        rapport.verifie("un niveau inconnu est refusé (ValueError)", True)

    rapport.section("3. Décroché en mode mariage : tonalité puis numérotation")
    with Banc() as banc:
        banc.decrocher(stabiliser=False)
        rapport.verifie("l'état passe à « decroche »",
                        banc.attendre_etat(livre_dor.STATE_DECROCHE),
                        f"état observé : {banc.etat}")
        rapport.verifie("la tonalité est jouée",
                        banc.attendre_lecture("tonalite.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("l'état passe ensuite à « numerotation »",
                        banc.attendre_etat(livre_dor.STATE_NUMEROTATION),
                        f"état observé : {banc.etat}")
        joues = banc.audio.noms_lus()
        rapport.verifie("aucun message n'est joué avant la composition",
                        not any(n.startswith("message_") for n in joues),
                        f"fichiers joués : {joues}")
        rapport.verifie("aucun enregistrement n'est ouvert au seul décroché",
                        banc.audio.noms_enregistres() == [],
                        f"enregistrés : {banc.audio.noms_enregistres()}")

    rapport.section("4. status.json publie l'état décroché (§5.2)")
    with harness.journal_status() as journal, Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        etats = [etat for etat, _ in journal]
        rapport.verifie("« decroche » est publié", livre_dor.STATE_DECROCHE in etats,
                        f"états publiés : {etats}")
        status = banc.status()
        rapport.verifie("status.json est relisible et horodaté",
                        bool(status) and bool(status.get("derniere_maj")),
                        f"status.json : {status}")

    rapport.section("5. Le cadran est purgé au décroché (reset_dial)")
    with Banc() as banc:
        # Chiffres « fantômes » composés combiné raccroché (cadran manipulé
        # entre deux invités) : ils ne doivent pas être consommés par l'appel
        # suivant, sous peine de lire un message sans que personne n'ait
        # composé.
        banc.composer(4, 7)
        banc.decrocher(stabiliser=False)
        rapport.verifie("l'état passe à « numerotation » (rien n'a été composé)",
                        banc.attendre_etat(livre_dor.STATE_NUMEROTATION),
                        f"état observé : {banc.etat}")
        time.sleep(0.4)
        joues = banc.audio.noms_lus()
        rapport.verifie("aucun message des mariés n'est lu avec un chiffre résiduel",
                        not any(n.startswith("message_") for n in joues),
                        f"fichiers joués : {joues}")
        rapport.verifie("la machine attend toujours un vrai chiffre",
                        banc.etat == livre_dor.STATE_NUMEROTATION,
                        f"état observé : {banc.etat}")

    rapport.section("6. Décroché juste après une sonnerie : appel entrant (§1.2)")
    with Banc(RING_ANSWER_GRACE_SEC=10) as banc:
        banc.declencher_sonnerie_a_distance()
        rapport.verifie("la sonnerie part", banc.attendre_lecture("ring_out.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.attendre_etat(livre_dor.STATE_ATTENTE)
        banc.decrocher(stabiliser=False)
        rapport.verifie("l'état passe à « appel_repondu »",
                        banc.attendre_etat(livre_dor.STATE_APPEL_REPONDU),
                        f"état observé : {banc.etat}")
        time.sleep(0.4)
        joues = banc.audio.noms_lus()
        rapport.verifie("aucune tonalité n'est jouée sur un appel entrant",
                        "tonalite.wav" not in joues, f"fichiers joués : {joues}")
        rapport.verifie("un message des mariés est joué directement",
                        any(n.startswith("message_") for n in joues),
                        f"fichiers joués : {joues}")

    rapport.section("7. Décroché en mode restitution (§5.7)")
    with harness.journal_status() as journal, Banc(restitution=True, messages=3) as banc:
        banc.decrocher()
        publies = [(etat, detail) for etat, detail in journal]
        rapport.verifie("« decroche » est publié avec le détail « mode restitution »",
                        (livre_dor.STATE_DECROCHE, "mode restitution") in publies,
                        f"états publiés : {publies}")
        rapport.verifie("la tonalité est jouée", banc.attendre_lecture("tonalite.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("l'état passe à « restitution_numerotation »",
                        banc.attendre_etat(livre_dor.STATE_RESTITUTION_NUMEROTATION),
                        f"état observé : {banc.etat}")
        rapport.verifie("aucun message des mariés n'est joué en restitution",
                        not banc.audio.a_lu("message_generique.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("8. Décroché vu par le dashboard (gpio_io.get_current_status)")
    inputs = gpio_io.PhoneInputs()
    with harness.remplacer(gpio_io, "_phone_inputs", inputs):
        inputs.set_hook(True)
        rapport.egal("le dashboard lit « DECROCHE »",
                     gpio_io.get_current_status()["hook"], "DECROCHE")
        inputs.set_hook(False)
        rapport.egal("le dashboard lit « raccroché »",
                     gpio_io.get_current_status()["hook"], "raccroché")


def test_reel(rapport: Rapport) -> None:
    """Vérification du câblage réel du crochet, sur le Raspberry Pi (§7.6 point 3)."""
    rapport.section("Matériel : contact du crochet")
    inputs = harness.exiger_gpio(rapport)
    try:
        etat_initial = inputs.is_hook_up()
        rapport.info(f"état lu au démarrage : "
                     f"{'DÉCROCHÉ' if etat_initial else 'raccroché'}")
        rapport.verifie("le combiné est raccroché au départ", etat_initial is False,
                        "reposez le combiné avant de lancer ce test, ou inversez "
                        "HOOK_ACTIVE_STATE si le sens logique est inversé")

        delai = harness.attendre_condition(inputs.is_hook_up, "Décrochez le combiné.")
        rapport.verifie("le décroché est détecté", delai is not None,
                        "aucun changement d'état : vérifiez le câblage du crochet "
                        f"sur GPIO {config.HOOK_PIN} et le sens logique "
                        f"HOOK_ACTIVE_STATE={config.HOOK_ACTIVE_STATE}")
        if delai is not None:
            rapport.verifie("la détection est immédiate (< 1s)", delai < 1.0,
                            f"détecté après {delai:.2f}s")

        delai = harness.attendre_condition(lambda: not inputs.is_hook_up(),
                                           "Raccrochez le combiné.")
        rapport.verifie("le raccroché est détecté", delai is not None)
    finally:
        gpio_io.cleanup()


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("DÉCROCHÉ", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport)
    else:
        test_simule(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
