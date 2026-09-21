#!/usr/bin/env python3
"""Test unitaire de la LECTURE D'UN MESSAGE (§1.2, §4.1, §5.7, §7.2).

Deux lectures très différentes cohabitent dans le projet :

- le **message des mariés**, choisi par le chiffre composé au cadran (ou tiré
  au hasard sur un appel entrant), joué avant le bip d'invitation ;
- le **message d'un invité**, relu en mode restitution après l'événement,
  désigné par son numéro d'ordre chronologique (§5.7).

Ce que vérifie ce script, seul et sans matériel :

1. la sélection du fichier à jouer dans les deux cas, replis compris ;
2. l'enchaînement message -> bip -> enregistrement ;
3. le tirage aléatoire ne répète jamais le message précédent ;
4. l'ordre chronologique 1..N des messages d'invités et son bornage ;
5. la commande aplay et la surveillance de son sous-processus : arrêt
   immédiat au raccroché, timeout, erreurs, aucun orphelin (§7.2).

Usage :
    python3 tests/test_lecture.py              # simulation, aucune dépendance
    python3 tests/test_lecture.py --reel       # écoute réelle sur la carte son
"""

import time
from pathlib import Path

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, PopenFactice, Rapport

import alsa_io                       # noqa: E402
import audio_io                      # noqa: E402
import config                        # noqa: E402
import gpio_io                       # noqa: E402
import livre_dor                     # noqa: E402


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Sélection du message des mariés (§1.2)")
    with Banc(machine=False, chiffres_maries=[0, 1, 2]) as banc:
        rapport.egal("le chiffre 1 joue message_1.wav",
                     livre_dor.message_path_for_digit(1).name, "message_1.wav")
        rapport.egal("le chiffre 0 joue message_0.wav",
                     livre_dor.message_path_for_digit(0).name, "message_0.wav")
        rapport.egal("un chiffre sans message retombe sur le générique",
                     livre_dor.message_path_for_digit(6).name, "message_generique.wav")
        candidats = sorted(p.name for p in livre_dor.available_random_messages())
        rapport.egal("le tirage aléatoire porte sur les messages présents",
                     candidats, ["message_0.wav", "message_1.wav", "message_2.wav",
                                 "message_generique.wav"])

    rapport.section("2. Enchaînement message -> bip -> enregistrement")
    with Banc() as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(7)
        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
        rapport.egal("l'ordre de lecture est tonalité, message, bip",
                     banc.audio.sequence_lue()[:3],
                     ["tonalite.wav", "message_7.wav", "bip.wav"])
        appels = {a.nom: a for a in banc.audio.lectures}
        rapport.verifie("la lecture est protégée par un timeout (§7.2)",
                        appels["message_7.wav"].timeout_sec == config.AUDIO_PLAY_TIMEOUT_SEC,
                        f"timeout transmis : {appels['message_7.wav'].timeout_sec}")
        rapport.verifie("la lecture sort sur la carte son par défaut",
                        appels["message_7.wav"].device is None,
                        f"device : {appels['message_7.wav'].device}")
        rapport.egal("le message part dans le combiné (sortie casque, §4.1)",
                     banc.audio.sortie_de("message_7.wav"), config.AUDIO_OUTPUT_COMBINE)
        rapport.egal("le bip aussi : il doit être entendu dans l'écouteur",
                     banc.audio.sortie_de("bip.wav"), config.AUDIO_OUTPUT_COMBINE)
        rapport.egal("la tonalité aussi",
                     banc.audio.sortie_de("tonalite.wav"), config.AUDIO_OUTPUT_COMBINE)
        rapport.verifie("toutes les lectures précisent une sortie (aucune au hasard)",
                        all(s is not None for s in banc.audio.sorties_utilisees()),
                        f"sorties : {banc.audio.sorties_utilisees()}")

    rapport.section("3. Message tiré au hasard sur un appel entrant (§1.2)")
    with Banc(machine=False, chiffres_maries=[1, 2]) as banc:
        machine = livre_dor.GuestBookStateMachine(banc.inputs)
        tirages = [machine._pick_random_message().name for _ in range(12)]
        rapport.verifie("tous les tirages sont des messages existants",
                        set(tirages) <= {"message_1.wav", "message_2.wav",
                                          "message_generique.wav"},
                        f"tirages : {tirages}")
        repetitions = [i for i in range(1, len(tirages)) if tirages[i] == tirages[i - 1]]
        rapport.verifie("un message n'est jamais joué deux fois de suite",
                        repetitions == [], f"tirages : {tirages}")
        rapport.verifie("plusieurs messages différents sortent",
                        len(set(tirages)) > 1, f"tirages : {tirages}")

    with Banc(machine=False, chiffres_maries=[], generique=True) as banc:
        machine = livre_dor.GuestBookStateMachine(banc.inputs)
        rapport.egal("avec un seul message disponible, il est rejoué",
                     [machine._pick_random_message().name for _ in range(3)],
                     ["message_generique.wav"] * 3)

    rapport.section("4. Numérotation chronologique des messages d'invités (§5.7)")
    with Banc(machine=False, messages=5) as banc:
        trouves = [p.name for p in livre_dor.recorded_messages()]
        rapport.egal("les messages sont triés chronologiquement, pas par date de copie",
                     trouves, banc.noms_messages)
        (config.MESSAGES_DIR / "message_2026-06-20_14-00-00.txt").write_text(
            "transcription", encoding="utf-8")
        (config.MESSAGES_DIR / "sous_dossier").mkdir()
        rapport.egal("les transcriptions .txt et les sous-dossiers sont ignorés",
                     [p.name for p in livre_dor.recorded_messages()], banc.noms_messages)

    with Banc(machine=False, messages=0) as banc:
        rapport.egal("un dossier messages/ vide ne lève pas d'exception (§7.4)",
                     livre_dor.recorded_messages(), [])
        rapport.egal("sans annonce dédiée, l'absence de message se signale par le bip",
                     livre_dor.restitution_absence_wav().name, "bip.wav")
    with Banc(machine=False, messages=0, aucun_message=True) as banc:
        rapport.egal("l'annonce aucun_message.wav est utilisée si elle existe",
                     livre_dor.restitution_absence_wav().name, "aucun_message.wav")

    rapport.section("5. Lecture d'un message d'invité en restitution (§5.7)")
    with Banc(restitution=True, messages=4, RESTITUTION_INTERDIGIT_SEC=0.4,
              RESTITUTION_SOUND_CARD="plughw:9,0") as banc:
        banc.decrocher()
        banc.composer(3)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("le numéro composé désigne le 3e message chronologique",
                     banc.messages_invites_lus(), [banc.noms_messages[2]])
        appel = [a for a in banc.audio.lectures if a.nom == banc.noms_messages[2]][0]
        rapport.egal("la lecture utilise le périphérique RESTITUTION_SOUND_CARD",
                     appel.device, "plughw:9,0")
        rapport.egal("aucun bip ni message des mariés n'est joué",
                     [n for n in banc.audio.noms_lus() if n.startswith(("bip", "message_g"))],
                     [])

    with Banc(restitution=True, messages=0, aucun_message=True) as banc:
        banc.decrocher()
        time.sleep(3 * harness.STABILISATION_SEC)
        rapport.verifie("sans aucun message, l'annonce d'absence est jouée",
                        banc.audio.a_lu("aucun_message.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        rapport.verifie("la tonalité n'invite pas à composer un numéro inutile",
                        not banc.audio.a_lu("tonalite.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("6. Commande aplay et surveillance du processus (§4.1, §7.2)")
    # doublure_audio=False : ici c'est audio_io.play lui-même qui est sous test,
    # seul subprocess.Popen est remplacé.
    with Banc(machine=False, doublure_audio=False) as banc:
        cible = config.MESSAGE_GENERIQUE_WAV

        popen = PopenFactice(duree=0.1)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.play(cible, should_continue=lambda: True,
                                      poll_interval=0.02)
        commande = popen.derniere_commande
        rapport.egal("la lecture va au bout", resultat, "completed")
        rapport.egal("la commande est aplay", commande[0], "aplay")
        rapport.egal("la carte son configurée est utilisée",
                     commande[1:3], ["-D", config.SOUND_CARD])
        rapport.egal("le fichier à jouer est le dernier argument",
                     commande[-1], str(cible))

        popen = PopenFactice(duree=0.1)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            audio_io.play(cible, should_continue=lambda: True, poll_interval=0.02,
                          device="plughw:9,0")
        rapport.egal("le périphérique peut être surchargé (mode restitution, §5.7)",
                     popen.derniere_commande[1:3], ["-D", "plughw:9,0"])

        # --- Commutation de la sortie du codec (§4.1) -------------------
        # La bascule doit précéder l'ouverture du PCM : faite après, les
        # premières dizaines de ms sortiraient du mauvais haut-parleur.
        banc.alsa.reinitialiser()
        ordre = []
        alsa_espion = harness.AlsaFactice()
        alsa_espion_select = alsa_espion.select_output

        def select_trace(output, force=False):
            ordre.append(("bascule", output))
            return alsa_espion_select(output, force)

        popen = PopenFactice(duree=0.1)
        with harness.remplacer(alsa_io, "select_output", select_trace):
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.play(cible, should_continue=lambda: True, poll_interval=0.02,
                              output="lineout")
                ordre.append(("aplay", popen.derniere_commande[-1]))
        rapport.egal("la sortie demandée est bien appliquée",
                     alsa_espion.bascules, ["lineout"])
        rapport.egal("la bascule précède le lancement d'aplay",
                     [etape for etape, _ in ordre], ["bascule", "aplay"])

        # Sans output=, aucune commutation : le mode restitution et les
        # appels internes gardent la sortie courante.
        alsa_espion = harness.AlsaFactice()
        popen = PopenFactice(duree=0.1)
        with harness.remplacer(alsa_io, "select_output", alsa_espion.select_output):
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.play(cible, should_continue=lambda: True, poll_interval=0.02)
        rapport.egal("sans sortie demandée, le codec n'est pas touché",
                     alsa_espion.bascules, [])

        # Une commutation en échec ne doit jamais empêcher la lecture : le
        # mauvais haut-parleur vaut mieux que le silence (§7.4).
        alsa_muet = harness.AlsaFactice(echouer=True)
        popen = PopenFactice(duree=0.1)
        with harness.remplacer(alsa_io, "select_output", alsa_muet.select_output):
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                resultat = audio_io.play(cible, should_continue=lambda: True,
                                         poll_interval=0.02, output="headphone")
        rapport.egal("une commutation en échec n'empêche pas la lecture",
                     resultat, "completed")

        # Raccroché pendant la lecture : arrêt immédiat du sous-processus.
        popen = PopenFactice(duree=60.0)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            debut = time.monotonic()
            resultat = audio_io.play(cible, should_continue=lambda: False,
                                      poll_interval=0.02)
            ecoule = time.monotonic() - debut
        rapport.egal("un should_continue() faux interrompt la lecture",
                     resultat, "interrupted")
        rapport.verifie("l'interruption est immédiate (< 0,5 s)", ecoule < 0.5,
                        f"interrompu après {ecoule:.2f}s")
        rapport.verifie("le sous-processus aplay est bien arrêté (aucun orphelin)",
                        popen.processus[0].terminate_appele is True)

        # Processus figé : le timeout doit reprendre la main (§7.2).
        popen = PopenFactice(duree=60.0)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.play(cible, should_continue=lambda: True,
                                      poll_interval=0.02, timeout_sec=0.2)
        rapport.egal("un aplay figé est arrêté par le timeout", resultat, "error")
        rapport.verifie("le sous-processus figé est tué (aucun orphelin)",
                        popen.processus[0].terminate_appele is True)

        popen = PopenFactice(duree=0.05, returncode=1, stderr=b"aplay: erreur")
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.play(cible, should_continue=lambda: True,
                                      poll_interval=0.02)
        rapport.egal("un aplay en échec est signalé", resultat, "error")

        popen = PopenFactice(erreur=OSError("aplay introuvable"))
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.play(cible, should_continue=lambda: True)
        rapport.egal("un aplay absent ne lève pas d'exception", resultat, "error")

        popen = PopenFactice()
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.play(config.AUDIO_DIR / "inexistant.wav",
                                      should_continue=lambda: True)
        rapport.egal("un fichier absent est signalé sans exception", resultat, "error")
        rapport.verifie("aucun processus n'est lancé pour un fichier absent",
                        popen.commandes == [], f"commandes : {popen.commandes}")


def test_reel(rapport: Rapport, fichier: Path = None) -> None:
    """Écoute réelle sur la carte son du téléphone (§7.6 point 4).

    Les fichiers joués sont ceux de l'installation (audio/, produits par
    prepare_audio.py) et le périphérique est celui de la configuration : ce
    test valide le panning tonalité/écouteur, le niveau sonore et la coupure
    au raccroché sur le matériel réel.
    """
    harness.resume_parametres(rapport, [
        "SOUND_CARD", "RESTITUTION_SOUND_CARD", "AUDIO_PLAY_TIMEOUT_SEC",
        "AUDIO_DIR", "MESSAGES_DIR",
    ])

    rapport.section("1. Prérequis")
    harness.exiger_carte_son(rapport)
    for chemin in (config.TONALITE_WAV, config.BIP_WAV, config.MESSAGE_GENERIQUE_WAV):
        rapport.verifie(f"{chemin.name} est présent", chemin.exists(),
                        f"{chemin} manquant — lancez `python3 src/prepare_audio.py`")

    inputs = None
    if harness.gpio_disponible():
        inputs = harness.exiger_gpio(rapport)

    try:
        if fichier is not None:
            rapport.section(f"2. Lecture de {fichier.name}")
            fichiers = [fichier]
        else:
            rapport.section("2. Lecture des fichiers du parcours invité")
            fichiers = [config.TONALITE_WAV, config.MESSAGE_GENERIQUE_WAV,
                        config.BIP_WAV]

        for chemin in fichiers:
            if inputs is not None:
                harness.attendre_condition(inputs.is_hook_up,
                                            "Décrochez le combiné.")
                # should_continue identique au service : raccrocher coupe le son.
                should_continue = inputs.is_hook_up
            else:
                should_continue = (lambda: True)
            print(f"\n  {harness.GRAS}>>> Lecture de {chemin.name}...{harness.RAZ}")
            debut = time.monotonic()
            resultat = audio_io.play(chemin, should_continue=should_continue,
                                      timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
            ecoule = time.monotonic() - debut
            rapport.info(f"résultat : {resultat} après {ecoule:.1f}s")
            rapport.verifie(f"{chemin.name} est joué sans erreur",
                            resultat in ("completed", "interrupted"),
                            f"résultat aplay : {resultat}")
            attendu = ("dans l'écouteur du combiné"
                       if chemin != config.RING_OUT_WAV else "sur le haut-parleur")
            rapport.verifie(f"{chemin.name} s'entend {attendu} (§4.2)",
                            harness.confirmer(f"Avez-vous entendu {chemin.name} "
                                               f"{attendu} ?"))
            if inputs is not None and resultat == "interrupted":
                rapport.verifie("le raccroché a bien coupé la lecture",
                                not inputs.is_hook_up())

        messages = livre_dor.recorded_messages()
        rapport.section("3. Relecture d'un message d'invité (mode restitution, §5.7)")
        if not messages:
            rapport.ignore("relecture d'un message d'invité",
                           f"aucun enregistrement dans {config.MESSAGES_DIR}")
        else:
            dernier = messages[-1]
            rapport.info(f"{len(messages)} message(s) disponible(s) ; "
                         f"lecture du n°{len(messages)} : {dernier.name}")
            if inputs is not None:
                harness.attendre_condition(inputs.is_hook_up, "Décrochez le combiné.")
            print(f"\n  {harness.GRAS}>>> Lecture de {dernier.name}...{harness.RAZ}")
            resultat = audio_io.play(
                dernier,
                should_continue=(inputs.is_hook_up if inputs is not None else (lambda: True)),
                timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
                device=config.RESTITUTION_SOUND_CARD)
            rapport.verifie("le message d'invité est relisible",
                            resultat in ("completed", "interrupted"),
                            f"résultat aplay : {resultat}")
            rapport.verifie("le message d'invité s'entend dans le combiné",
                            harness.confirmer("Avez-vous entendu l'enregistrement ?"))
    finally:
        if inputs is not None:
            gpio_io.cleanup()


def main() -> None:
    parser = harness.parseur(__doc__)
    parser.add_argument("--fichier", type=Path, default=None,
                         help="Joue ce fichier WAV au lieu des fichiers du "
                              "parcours invité (--reel).")
    args = parser.parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("LECTURE D'UN MESSAGE", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport, args.fichier)
    else:
        test_simule(rapport)
        harness.indice_materiel()
    rapport.conclure()


if __name__ == "__main__":
    main()
