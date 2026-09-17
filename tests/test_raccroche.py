#!/usr/bin/env python3
"""Test unitaire de la fonction RACCROCHÉ (§1.2, §5.1, §7.2).

Le raccroché est la seule commande universelle du parcours invité : il doit
interrompre *immédiatement* ce qui est en cours, à n'importe quel moment, et
ramener le téléphone en attente sans laisser de sous-processus derrière lui.

Ce que vérifie ce script, seul et sans matériel :

1. le contact du crochet retombe bien au raccroché ;
2. raccroché pendant la tonalité, la numérotation, le message, le bip :
   tout s'arrête et rien ne s'enchaîne ;
3. raccroché pendant l'enregistrement : l'enregistrement s'arrête et le
   fichier déjà écrit est conservé (jamais supprimé, §7.2) ;
4. une micro-coupure du crochet (faux contact) ne tronque pas le message —
   anti-rebond RECORDING_HANGUP_CONFIRM_SEC (§7.2) ;
5. le téléphone revient toujours à l'état « attente » ;
6. en mode restitution, le raccroché interrompt la lecture (§5.7).

Usage :
    python3 tests/test_raccroche.py           # simulation, aucune dépendance
    python3 tests/test_raccroche.py --reel    # sur le Raspberry Pi câblé
"""

import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import AudioFactice, Banc, Rapport

import config                        # noqa: E402
import gpio_io                       # noqa: E402
import livre_dor                     # noqa: E402


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Contact du crochet (gpio_io.PhoneInputs)")
    inputs = gpio_io.PhoneInputs()
    inputs.set_hook(True)
    inputs.set_hook(False)
    rapport.verifie("après un raccroché, is_hook_up() est faux",
                    inputs.is_hook_up() is False)

    rapport.section("2. Raccroché pendant la tonalité")
    with Banc() as banc:
        banc.decrocher(stabiliser=False)
        banc.attendre_lecture("tonalite.wav")
        banc.raccrocher()
        rapport.verifie("la tonalité est interrompue",
                        "tonalite.wav" in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        rapport.verifie("retour à l'état « attente »",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")
        rapport.verifie("aucun message n'est lu",
                        not any(n.startswith("message_") for n in banc.audio.noms_lus()),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("aucun enregistrement n'est créé",
                        banc.audio.noms_enregistres() == [])

    rapport.section("3. Raccroché pendant la numérotation")
    with Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.raccrocher()
        rapport.verifie("retour à l'état « attente »",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")
        rapport.verifie("aucun message n'est lu",
                        not any(n.startswith("message_") for n in banc.audio.noms_lus()),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("4. Raccroché pendant la lecture du message des mariés")
    with Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        rapport.verifie("le message du chiffre composé démarre",
                        banc.attendre_lecture("message_3.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.raccrocher()
        rapport.verifie("la lecture est interrompue",
                        "message_3.wav" in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        rapport.verifie("le bip n'est pas joué", not banc.audio.a_lu("bip.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("aucun enregistrement n'est créé",
                        banc.audio.noms_enregistres() == [])
        rapport.verifie("retour à l'état « attente »",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")

    rapport.section("5. Raccroché pendant le bip")
    with Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        rapport.verifie("le bip est atteint", banc.attendre_lecture("bip.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.raccrocher()
        rapport.verifie("le bip est interrompu",
                        "bip.wav" in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        rapport.verifie("aucun enregistrement n'est créé",
                        banc.audio.noms_enregistres() == [],
                        f"enregistrés : {banc.audio.noms_enregistres()}")

    rapport.section("6. Raccroché pendant l'enregistrement (§7.2)")
    # Enregistrement volontairement long pour pouvoir raccrocher au milieu.
    audio = AudioFactice(duree_enregistrement=3.0)
    with Banc(audio=audio) as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        rapport.verifie("l'enregistrement démarre",
                        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0),
                        f"état observé : {banc.etat}")
        time.sleep(0.2)
        banc.raccrocher()
        time.sleep(0.3)
        enregistrements = audio.enregistrements
        rapport.verifie("un enregistrement a bien été ouvert", len(enregistrements) == 1,
                        f"enregistrements : {enregistrements}")
        if enregistrements:
            rapport.egal("l'enregistrement est arrêté par le raccroché",
                         enregistrements[0].resultat, "interrupted")
            rapport.verifie("le fichier déjà écrit est conservé (jamais supprimé)",
                            enregistrements[0].chemin.exists(),
                            f"fichier : {enregistrements[0].chemin}")
        rapport.verifie("retour à l'état « attente »",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")

    rapport.section("7. Anti-rebond du raccroché (livre_dor.HangupConfirmer)")
    inputs = gpio_io.PhoneInputs()
    inputs.set_hook(True)
    confirmeur = livre_dor.HangupConfirmer(inputs, confirm_sec=0.3)
    rapport.verifie("combiné décroché : l'enregistrement continue",
                    confirmeur.should_continue() is True)
    inputs.set_hook(False)
    rapport.verifie("crochet retombé : pas encore confirmé (fenêtre ouverte)",
                    confirmeur.should_continue() is True)
    inputs.set_hook(True)
    rapport.verifie("micro-coupure terminée : l'enregistrement continue",
                    confirmeur.should_continue() is True)
    inputs.set_hook(False)
    confirmeur.should_continue()          # démarre le compte à rebours
    time.sleep(0.35)
    rapport.verifie("crochet resté bas plus de confirm_sec : raccroché confirmé",
                    confirmeur.should_continue() is False)
    defaut = livre_dor.HangupConfirmer(inputs)
    rapport.egal("le seuil par défaut vient de la configuration",
                 defaut.confirm_sec, config.RECORDING_HANGUP_CONFIRM_SEC)

    rapport.section("8. Une micro-coupure ne tronque pas l'enregistrement (§7.2)")
    audio = AudioFactice(duree_enregistrement=2.0)
    with Banc(audio=audio, RECORDING_HANGUP_CONFIRM_SEC=0.5) as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
        time.sleep(0.2)
        banc.inputs.set_hook(False)       # faux contact de 100 ms
        time.sleep(0.1)
        banc.inputs.set_hook(True)
        time.sleep(0.4)
        rapport.verifie("l'enregistrement est toujours en cours",
                        audio.enregistrements and audio.enregistrements[0].resultat != "interrupted",
                        f"enregistrements : {audio.enregistrements}")
        rapport.verifie("l'état reste « enregistrement »",
                        banc.etat == livre_dor.STATE_ENREGISTREMENT,
                        f"état observé : {banc.etat}")

    rapport.section("9. Raccroché en mode restitution (§5.7)")
    with Banc(restitution=True, messages=3, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(2)
        rapport.verifie("le 2e message est lu",
                        banc.attendre_lecture(banc.noms_messages[1], timeout=3.0),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.raccrocher()
        rapport.verifie("la lecture est interrompue",
                        banc.noms_messages[1] in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        rapport.verifie("retour à l'état « attente »",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")
        rapport.verifie("aucun enregistrement n'est créé en restitution",
                        banc.audio.noms_enregistres() == [])

    rapport.section("10. Le raccroché sort de l'attente du raccroché (§5.7)")
    with Banc(restitution=True, messages=2, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(1)
        rapport.verifie("la lecture va au bout puis attend le raccroché",
                        banc.attendre_etat(livre_dor.STATE_RESTITUTION_LECTURE, timeout=3.0))
        time.sleep(0.5)
        rapport.verifie("le téléphone reste silencieux combiné décroché",
                        banc.etat == livre_dor.STATE_RESTITUTION_LECTURE,
                        f"état observé : {banc.etat}")
        banc.raccrocher()
        rapport.verifie("le raccroché ramène en attente",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE),
                        f"état observé : {banc.etat}")


def test_reel(rapport: Rapport) -> None:
    """Vérification du câblage réel du crochet au raccroché (§7.6 point 3)."""
    rapport.section("Matériel : détection du raccroché")
    inputs = harness.exiger_gpio(rapport)
    try:
        delai = harness.attendre_condition(inputs.is_hook_up, "Décrochez le combiné.")
        rapport.verifie("le décroché est détecté", delai is not None,
                        f"vérifiez le câblage du crochet sur GPIO {config.HOOK_PIN}")

        delai = harness.attendre_condition(lambda: not inputs.is_hook_up(),
                                           "Raccrochez le combiné.")
        rapport.verifie("le raccroché est détecté", delai is not None,
                        "le crochet reste vu comme décroché : sens logique "
                        f"HOOK_ACTIVE_STATE={config.HOOK_ACTIVE_STATE} à vérifier")
        if delai is not None:
            rapport.verifie("la détection est immédiate (< 1s)", delai < 1.0,
                            f"détecté après {delai:.2f}s — anti-rebond "
                            f"HOOK_DEBOUNCE_SEC={config.HOOK_DEBOUNCE_SEC}s")

        rapport.info("test de stabilité : le crochet ne doit pas « rebondir » "
                     "tout seul pendant 5 s, combiné posé.")
        transitions = 0
        precedent = inputs.is_hook_up()
        fin = time.monotonic() + 5.0
        while time.monotonic() < fin:
            courant = inputs.is_hook_up()
            if courant != precedent:
                transitions += 1
                precedent = courant
            time.sleep(0.02)
        rapport.verifie("aucun faux contact au repos", transitions == 0,
                        f"{transitions} changement(s) d'état détecté(s) : contact "
                        "sale ou anti-rebond HOOK_DEBOUNCE_SEC trop court")
    finally:
        gpio_io.cleanup()


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("RACCROCHÉ", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport)
    else:
        test_simule(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
