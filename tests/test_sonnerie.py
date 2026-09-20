#!/usr/bin/env python3
"""Test unitaire de la SONNERIE (§1.2, §5.1, §5.7).

La sonnerie part toute seule toutes les RING_INTERVAL_SEC secondes, ou à la
demande depuis le dashboard via le fichier drapeau ring_trigger. Décrocher
pendant la sonnerie — ou dans les RING_ANSWER_GRACE_SEC qui la suivent —
simule un appel entrant : message tiré au hasard, sans tonalité ni cadran.

Ce que vérifie ce script, seul et sans matériel :

1. la consommation atomique du drapeau ring_trigger (§5.1, §8) ;
2. la sonnerie périodique et son réarmement ;
3. la sonnerie déclenchée à distance ;
4. l'interruption immédiate de la sonnerie au décroché ;
5. la fenêtre de grâce : appel entrant juste après la sonnerie, parcours
   normal au-delà ;
6. l'absence totale de sonnerie en mode restitution (§5.7).

Usage :
    python3 tests/test_sonnerie.py             # simulation, aucune dépendance
    python3 tests/test_sonnerie.py --reel      # sonnerie réelle sur le matériel
"""

import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import audio_io                      # noqa: E402
import config                        # noqa: E402
import gpio_io                       # noqa: E402
import livre_dor                     # noqa: E402
import mode_io                       # noqa: E402


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Drapeau ring_trigger (§5.1, §8)")
    with Banc(machine=False) as banc:
        rapport.verifie("sans drapeau, rien n'est déclenché",
                        livre_dor.consume_ring_trigger() is False)
        banc.declencher_sonnerie_a_distance()
        rapport.verifie("le drapeau existe une fois créé",
                        config.RING_TRIGGER_FILE.exists())
        rapport.verifie("le drapeau est consommé", livre_dor.consume_ring_trigger() is True)
        rapport.verifie("le fichier est supprimé après consommation",
                        not config.RING_TRIGGER_FILE.exists())
        rapport.verifie("une seconde consommation ne redéclenche rien",
                        livre_dor.consume_ring_trigger() is False)

    rapport.section("2. Sonnerie périodique (RING_INTERVAL_SEC)")
    with harness.journal_status() as journal, Banc(RING_INTERVAL_SEC=1) as banc:
        rapport.verifie("aucune sonnerie avant l'échéance",
                        not banc.audio.a_lu("ring_out.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("la sonnerie part à l'échéance",
                        banc.attendre_lecture("ring_out.wav", timeout=2.5),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("l'état « sonnerie » est publié dans status.json",
                        livre_dor.STATE_SONNERIE in [etat for etat, _ in journal],
                        f"états publiés : {journal}")
        rapport.verifie("la sonnerie se termine d'elle-même",
                        banc.audio.attendre_fin("ring_out.wav"),
                        "la lecture de ring_out.wav ne s'est pas terminée")
        rapport.verifie("le téléphone revient en attente aussitôt après",
                        banc.attendre_etat(livre_dor.STATE_ATTENTE, timeout=1.0),
                        f"état observé : {banc.etat}")
        # Sans republication, le dashboard resterait sur « sonnerie » jusqu'au
        # prochain battement, soit STATUS_HEARTBEAT_SEC (120 s par défaut).
        index_sonnerie = [i for i, (etat, _) in enumerate(journal)
                          if etat == livre_dor.STATE_SONNERIE]
        apres = journal[index_sonnerie[-1] + 1:] if index_sonnerie else []
        rapport.verifie("status.json ne reste pas figé sur « sonnerie » (§5.2)",
                        apres[:1] == [(livre_dor.STATE_ATTENTE, None)],
                        f"états publiés après la sonnerie : {apres}")
        rapport.verifie("elle ne repart pas aussitôt (pas de sonnerie en boucle)",
                        len(banc.audio.lectures_de("ring_out.wav")) == 1,
                        f"{len(banc.audio.lectures_de('ring_out.wav'))} sonneries "
                        "immédiatement enchaînées")
        rapport.verifie("la sonnerie repart à l'échéance suivante",
                        banc.audio.attendre_nb_lectures("ring_out.wav", 2, timeout=3.0),
                        f"sonneries : {len(banc.audio.lectures_de('ring_out.wav'))}")
        sonneries = banc.audio.lectures_de("ring_out.wav")
        if len(sonneries) >= 2:
            intervalle = sonneries[1].debut - sonneries[0].fin
            rapport.verifie("l'intervalle configuré est respecté entre deux sonneries",
                            0.8 <= intervalle <= 1.6,
                            f"{intervalle:.2f}s entre deux sonneries pour "
                            f"RING_INTERVAL_SEC={config.RING_INTERVAL_SEC}s")

    rapport.section("3. Sonnerie déclenchée à distance depuis le dashboard (§5.2)")
    with Banc() as banc:
        banc.declencher_sonnerie_a_distance()
        rapport.verifie("la sonnerie part immédiatement",
                        banc.attendre_lecture("ring_out.wav", timeout=1.5),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("le drapeau est consommé",
                        not config.RING_TRIGGER_FILE.exists())

    rapport.section("4. Décroché pendant la sonnerie : appel entrant (§1.2)")
    with Banc(RING_ANSWER_GRACE_SEC=5) as banc:
        banc.declencher_sonnerie_a_distance()
        banc.attendre_etat(livre_dor.STATE_SONNERIE, timeout=1.5)
        banc.decrocher(stabiliser=False)
        rapport.verifie("l'état passe à « appel_repondu »",
                        banc.attendre_etat(livre_dor.STATE_APPEL_REPONDU, timeout=2.0),
                        f"état observé : {banc.etat}")
        rapport.verifie("la sonnerie est interrompue",
                        "ring_out.wav" in banc.audio.noms_interrompus(),
                        f"interrompus : {banc.audio.noms_interrompus()}")
        time.sleep(0.4)
        joues = banc.audio.noms_lus()
        rapport.verifie("aucune tonalité n'est jouée", "tonalite.wav" not in joues,
                        f"fichiers joués : {joues}")
        rapport.verifie("un message des mariés est joué directement",
                        any(n.startswith("message_") for n in joues),
                        f"fichiers joués : {joues}")

        # Le câblage sépare les deux sorties du codec (§4.1) : la sonnerie
        # part sur le haut-parleur du line out, le message doit revenir dans
        # les écouteurs. C'est la commutation la plus critique du parcours,
        # puisque les deux se suivent immédiatement.
        rapport.egal("la sonnerie part sur le haut-parleur de sonnerie",
                     banc.audio.sortie_de("ring_out.wav"), config.AUDIO_OUTPUT_SONNERIE)
        message = next((n for n in joues if n.startswith("message_")), None)
        rapport.egal("le message qui suit revient dans le combiné",
                     banc.audio.sortie_de(message) if message else None,
                     config.AUDIO_OUTPUT_COMBINE)
        rapport.verifie("le codec a bien été commuté dans cet ordre",
                        banc.alsa.bascules[:2] == [config.AUDIO_OUTPUT_SONNERIE,
                                                    config.AUDIO_OUTPUT_COMBINE],
                        f"bascules : {banc.alsa.bascules}")

    rapport.section("5. Fenêtre de grâce après la sonnerie (RING_ANSWER_GRACE_SEC)")
    with Banc(RING_ANSWER_GRACE_SEC=5) as banc:
        banc.declencher_sonnerie_a_distance()
        banc.attendre_lecture("ring_out.wav", timeout=1.5)
        rapport.verifie("la sonnerie se termine", banc.audio.attendre_fin("ring_out.wav"),
                        "la lecture de ring_out.wav ne s'est pas terminée")
        banc.decrocher(stabiliser=False)
        rapport.verifie("décrocher juste après la sonnerie donne un appel entrant",
                        banc.attendre_etat(livre_dor.STATE_APPEL_REPONDU, timeout=2.0),
                        f"état observé : {banc.etat}")

    with Banc(RING_ANSWER_GRACE_SEC=1) as banc:
        banc.declencher_sonnerie_a_distance()
        banc.attendre_lecture("ring_out.wav", timeout=1.5)
        banc.audio.attendre_fin("ring_out.wav")
        time.sleep(1.3)                       # au-delà de la fenêtre de grâce
        banc.decrocher(stabiliser=False)
        rapport.verifie("passé la fenêtre, on retombe sur le parcours normal",
                        banc.attendre_etat(livre_dor.STATE_DECROCHE, timeout=2.0),
                        f"état observé : {banc.etat}")
        rapport.verifie("la tonalité est jouée",
                        banc.attendre_lecture("tonalite.wav", timeout=1.5),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("6. Aucune sonnerie en mode restitution (§5.7)")
    with Banc(restitution=True, messages=2, RING_INTERVAL_SEC=1) as banc:
        time.sleep(2.5)                       # deux échéances de sonnerie
        rapport.verifie("la sonnerie ne part jamais",
                        not banc.audio.a_lu("ring_out.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.declencher_sonnerie_a_distance()
        time.sleep(0.5)
        rapport.verifie("un déclenchement à distance reste sans effet",
                        not banc.audio.a_lu("ring_out.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("le drapeau est quand même consommé "
                        "(pas de sonnerie surprise au retour en mode mariage)",
                        not config.RING_TRIGGER_FILE.exists())

    rapport.section("7. Retour en mode mariage : l'intervalle est réarmé (§5.7)")
    with Banc(restitution=True, RING_INTERVAL_SEC=1) as banc:
        time.sleep(1.5)                       # l'échéance serait largement passée
        mode_io.write_mode(False)             # bascule depuis le dashboard
        time.sleep(0.3)
        rapport.verifie("aucune sonnerie immédiate au moment de la bascule",
                        not banc.audio.a_lu("ring_out.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("la sonnerie reprend à l'échéance suivante",
                        banc.attendre_lecture("ring_out.wav", timeout=2.5),
                        f"fichiers joués : {banc.audio.noms_lus()}")


def test_reel(rapport: Rapport) -> None:
    """Sonnerie réelle sur le téléphone assemblé (§7.6 points 4 et 5).

    Le fichier joué est celui de l'installation (audio/ring_out.wav, produit
    par prepare_audio.py) sur le périphérique configuré : ce test valide que
    la sonnerie sort bien sur le haut-parleur externe — et non dans
    l'écouteur — et qu'un décroché la coupe instantanément.
    """
    harness.resume_parametres(rapport, [
        "SOUND_CARD", "RING_INTERVAL_SEC", "RING_ANSWER_GRACE_SEC",
        "AUDIO_PLAY_TIMEOUT_SEC", "RING_TRIGGER_FILE",
    ])

    rapport.section("1. Prérequis")
    harness.exiger_carte_son(rapport)
    rapport.verifie("ring_out.wav est présent", config.RING_OUT_WAV.exists(),
                    f"{config.RING_OUT_WAV} manquant — placez une sonnerie dans "
                    "audio_src/ puis lancez `python3 src/prepare_audio.py`")
    if not config.RING_OUT_WAV.exists():
        return

    rapport.section("2. Drapeau ring_trigger sur l'installation réelle (§5.2)")
    if config.RING_TRIGGER_FILE.exists():
        rapport.ignore("consommation du drapeau ring_trigger",
                       "un déclenchement est déjà en attente (service en cours ?)")
    else:
        config.RING_TRIGGER_FILE.write_text("", encoding="utf-8")
        rapport.verifie("le drapeau écrit par le dashboard est consommé",
                        livre_dor.consume_ring_trigger() is True)
        rapport.verifie("le fichier est bien supprimé",
                        not config.RING_TRIGGER_FILE.exists())

    inputs = None
    if harness.gpio_disponible():
        inputs = harness.exiger_gpio(rapport)
    try:
        rapport.section("3. Sonnerie réelle")
        if inputs is not None:
            rapport.verifie("le combiné est raccroché avant de sonner",
                            not inputs.is_hook_up(), "reposez le combiné")
            print(f"\n  {harness.GRAS}>>> La sonnerie va retentir : décrochez "
                  f"pour la couper.{harness.RAZ}")
            should_continue = lambda: not inputs.is_hook_up()
        else:
            rapport.ignore("coupure de la sonnerie au décroché",
                           "RPi.GPIO indisponible")
            print(f"\n  {harness.GRAS}>>> La sonnerie va retentir...{harness.RAZ}")
            should_continue = lambda: True

        debut = time.monotonic()
        resultat = audio_io.play(config.RING_OUT_WAV, should_continue=should_continue,
                                  timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
        ecoule = time.monotonic() - debut
        rapport.info(f"résultat : {resultat} après {ecoule:.1f}s")
        rapport.verifie("la sonnerie est jouée sans erreur",
                        resultat in ("completed", "interrupted"),
                        f"résultat aplay : {resultat}")
        rapport.verifie("la sonnerie sort sur le haut-parleur externe (§4.2)",
                        harness.confirmer("Avez-vous entendu la sonnerie sur le "
                                           "haut-parleur externe (et non dans "
                                           "l'écouteur) ?"))
        if inputs is not None:
            rapport.egal("le décroché coupe la sonnerie immédiatement",
                         resultat, "interrupted")
            harness.attendre_condition(lambda: not inputs.is_hook_up(),
                                        "Raccrochez le combiné.")
    finally:
        if inputs is not None:
            gpio_io.cleanup()


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("SONNERIE", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport)
    else:
        test_simule(rapport)
        harness.indice_materiel()
    rapport.conclure()


if __name__ == "__main__":
    main()
