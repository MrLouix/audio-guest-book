#!/usr/bin/env python3
"""Test unitaire de l'ENREGISTREMENT D'UN MESSAGE (§1.2, §4.1, §7.2, §7.3).

C'est la fonction qui produit la seule donnée irremplaçable du projet : le
message d'un invité. Les règles vérifiées ici tournent donc toutes autour du
même principe — ne jamais perdre ni écraser un enregistrement.

Ce que vérifie ce script, seul et sans matériel :

1. le nom de fichier horodaté n'écrase jamais un enregistrement existant ;
2. l'enregistrement démarre bien après le bip, dans les deux scénarios
   (appel sortant et appel entrant) ;
3. la durée maximale MAX_RECORD_SEC est transmise à arecord ;
4. un enregistrement très court est conservé (jamais supprimé, §7.2) ;
5. l'espace disque est contrôlé avant d'enregistrer (§7.3) : refus en
   « critique », enregistrement signalé en « alerte » ;
6. aucun enregistrement n'est possible en mode restitution (§5.7) ;
7. la commande arecord est correctement construite et son sous-processus
   correctement surveillé (arrêt immédiat, aucun orphelin, §7.2).

Usage :
    python3 tests/test_enregistrement.py         # simulation, aucune dépendance
    python3 tests/test_enregistrement.py --reel  # enregistre vraiment 5 s
"""

import collections
import datetime
import shutil
import time
from pathlib import Path

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import AudioFactice, Banc, PopenFactice, Rapport

import audio_io                      # noqa: E402
import config                        # noqa: E402
import gpio_io                        # noqa: E402
import livre_dor                     # noqa: E402
import mode_io                        # noqa: E402

_Usage = collections.namedtuple("_Usage", "total used free")


def _usage_factice(libre_mo: float):
    """Remplaçant de shutil.disk_usage annonçant `libre_mo` mégaoctets libres."""
    def disk_usage(_chemin):
        return _Usage(total=0, used=0, free=int(libre_mo * 1024 * 1024))
    return disk_usage


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Nom de fichier horodaté (§7.2)")
    with Banc(machine=False) as banc:
        instant = datetime.datetime(2026, 6, 20, 14, 30, 5)
        chemin = livre_dor.timestamped_recording_path(instant)
        rapport.egal("le nom porte l'horodatage de l'enregistrement",
                     chemin.name, "message_2026-06-20_14-30-05.wav")
        rapport.verifie("le fichier est créé dans messages/",
                        chemin.parent == config.MESSAGES_DIR,
                        f"dossier : {chemin.parent}")

        chemin.write_bytes(b"RIFF")
        collision = livre_dor.timestamped_recording_path(instant)
        rapport.egal("un fichier existant n'est jamais écrasé",
                     collision.name, "message_2026-06-20_14-30-05_1.wav")
        collision.write_bytes(b"RIFF")
        rapport.egal("les collisions suivantes s'incrémentent",
                     livre_dor.timestamped_recording_path(instant).name,
                     "message_2026-06-20_14-30-05_2.wav")

    rapport.section("2. Enregistrement du parcours nominal (appel sortant)")
    with Banc(MAX_RECORD_SEC=90) as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        rapport.verifie("l'état passe à « enregistrement » après le bip",
                        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0),
                        f"état observé : {banc.etat}")
        rapport.verifie("le message est bien joué avant le bip",
                        banc.audio.noms_lus()[:3] ==
                        ["tonalite.wav", "message_3.wav", "bip.wav"],
                        f"fichiers joués : {banc.audio.noms_lus()}")
        time.sleep(0.4)
        crees = banc.enregistrements_crees()
        rapport.egal("un fichier est créé dans messages/", len(crees), 1)
        if crees:
            rapport.verifie("il porte un nom horodaté",
                            livre_dor.RECORDING_NAME_RE.match(crees[0].stem) is not None,
                            f"nom : {crees[0].name}")
        if banc.audio.enregistrements:
            rapport.egal("la durée maximale MAX_RECORD_SEC est transmise",
                         banc.audio.enregistrements[0].max_duration_sec, 90)

    rapport.section("3. Enregistrement du scénario « appel entrant » (§1.2)")
    with Banc(RING_ANSWER_GRACE_SEC=10) as banc:
        banc.declencher_sonnerie_a_distance()
        banc.attendre_lecture("ring_out.wav")
        banc.attendre_etat(livre_dor.STATE_ATTENTE)
        banc.decrocher()
        rapport.verifie("l'enregistrement est atteint sans composer de chiffre",
                        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0),
                        f"état observé : {banc.etat}")
        time.sleep(0.4)
        rapport.egal("un fichier est créé dans messages/",
                     len(banc.enregistrements_crees()), 1)

    rapport.section("4. Durée d'un enregistrement (livre_dor.wav_duration_sec)")
    with Banc(machine=False) as banc:
        court = harness.ecrire_wav(config.MESSAGES_DIR / "court.wav", secondes=0.5)
        rapport.verifie("la durée d'un WAV est mesurée correctement",
                        abs(livre_dor.wav_duration_sec(court) - 0.5) < 0.01,
                        f"mesuré : {livre_dor.wav_duration_sec(court)}s")
        tronque = config.MESSAGES_DIR / "tronque.wav"
        tronque.write_bytes(b"RIFF\x00\x00")
        rapport.egal("un fichier tronqué ne lève pas d'exception",
                     livre_dor.wav_duration_sec(tronque), 0.0)
        rapport.egal("un fichier absent ne lève pas d'exception",
                     livre_dor.wav_duration_sec(config.MESSAGES_DIR / "absent.wav"), 0.0)

    rapport.section("5. Un enregistrement très court est conservé (§7.2)")
    audio = AudioFactice(secondes_enregistrees=0.5)
    with Banc(audio=audio, SHORT_RECORDING_THRESHOLD_SEC=2.0) as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
        time.sleep(0.5)
        crees = banc.enregistrements_crees()
        rapport.egal("le fichier très court est toujours là", len(crees), 1)
        if crees:
            rapport.verifie("il dure bien moins que le seuil",
                            livre_dor.wav_duration_sec(crees[0]) <
                            config.SHORT_RECORDING_THRESHOLD_SEC,
                            f"durée : {livre_dor.wav_duration_sec(crees[0])}s")

    rapport.section("6. Contrôle de l'espace disque (§7.3)")
    with Banc(machine=False, DISK_WARNING_MB=500, DISK_CRITICAL_MB=100) as banc:
        with harness.remplacer(shutil, "disk_usage", _usage_factice(2000)):
            rapport.egal("2 Go libres -> ok",
                         livre_dor.disk_space_state(config.MESSAGES_DIR), "ok")
        with harness.remplacer(shutil, "disk_usage", _usage_factice(300)):
            rapport.egal("300 Mo libres -> alerte",
                         livre_dor.disk_space_state(config.MESSAGES_DIR), "alerte")
        with harness.remplacer(shutil, "disk_usage", _usage_factice(50)):
            rapport.egal("50 Mo libres -> critique",
                         livre_dor.disk_space_state(config.MESSAGES_DIR), "critique")

        def disque_illisible(_chemin):
            raise OSError("volume illisible")
        with harness.remplacer(shutil, "disk_usage", disque_illisible):
            rapport.egal("un disque illisible ne bloque pas l'enregistrement (§7.4)",
                         livre_dor.disk_space_state(config.MESSAGES_DIR), "ok")

    rapport.section("7. Espace disque critique : enregistrement refusé (§7.3)")
    with harness.journal_status() as journal, Banc(DISK_CRITICAL_MB=100) as banc:
        with harness.remplacer(shutil, "disk_usage", _usage_factice(50)):
            banc.decrocher()
            banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
            banc.composer(3)
            banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
            time.sleep(0.4)
            rapport.egal("aucun enregistrement n'est ouvert",
                         banc.audio.noms_enregistres(), [])
            rapport.egal("aucun fichier n'est créé",
                         banc.enregistrements_crees(), [])
            rapport.verifie("l'état « erreur » est publié pour le dashboard",
                            (livre_dor.STATE_ERREUR, "espace disque critique") in journal,
                            f"états publiés : {journal}")
            rapport.verifie("le refus est tracé dans status.json",
                            (livre_dor.STATE_ENREGISTREMENT,
                             "enregistrement refusé (espace disque critique)") in journal,
                            f"états publiés : {journal}")

    rapport.section("8. Espace disque en alerte : enregistrement quand même (§7.3)")
    with harness.journal_status() as journal, Banc(DISK_WARNING_MB=500,
                                                   DISK_CRITICAL_MB=100) as banc:
        with harness.remplacer(shutil, "disk_usage", _usage_factice(300)):
            banc.decrocher()
            banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
            banc.composer(3)
            banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
            time.sleep(0.4)
            rapport.egal("le message de l'invité est bien enregistré",
                         len(banc.enregistrements_crees()), 1)
            rapport.verifie("l'alerte disque est signalée dans status.json",
                            (livre_dor.STATE_ENREGISTREMENT, "espace disque faible")
                            in journal, f"états publiés : {journal}")

    rapport.section("9. Garde-fou du mode restitution (§5.7)")
    # Aucun parcours du mode restitution n'appelle l'enregistrement, mais une
    # bascule de mode pendant une communication déjà engagée le pourrait.
    with Banc(messages=3, restitution=False) as banc:
        mode_io.write_mode(True)
        banc.machine._run_enregistrement()
        rapport.egal("l'enregistrement est refusé", banc.audio.noms_enregistres(), [])
        rapport.egal("aucun fichier n'est ajouté dans messages/",
                     len(list(config.MESSAGES_DIR.glob("*.wav"))), banc.nb_messages)

    rapport.section("10. Commande arecord et surveillance du processus (§4.1, §7.2)")
    # doublure_audio=False : ici c'est audio_io.record lui-même qui est sous
    # test, seul subprocess.Popen est remplacé.
    with Banc(machine=False, doublure_audio=False) as banc:
        cible = config.MESSAGES_DIR / "essai.wav"
        popen = PopenFactice(duree=0.1)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.record(cible, max_duration_sec=120,
                                       should_continue=lambda: True,
                                       poll_interval=0.02)
        commande = popen.derniere_commande
        rapport.egal("l'enregistrement va au bout", resultat, "completed")
        rapport.egal("la commande est arecord", commande[0], "arecord")
        rapport.verifie("la carte son configurée est utilisée",
                        ["-D", config.SOUND_CARD] == commande[1:3], f"commande : {commande}")
        rapport.verifie("le format est bien WAV mono 44,1 kHz 16 bits (§4.1)",
                        ["-f", "S16_LE"] == commande[3:5]
                        and ["-c", "1"] == commande[5:7]
                        and ["-r", "44100"] == commande[7:9],
                        f"commande : {commande}")
        rapport.verifie("la durée maximale est passée en filet de sécurité",
                        ["-d", "120"] == commande[9:11], f"commande : {commande}")
        rapport.egal("le fichier cible est le dernier argument",
                     commande[-1], str(cible))

        # Raccroché : le sous-processus doit être arrêté immédiatement.
        popen = PopenFactice(duree=60.0)
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.record(cible, max_duration_sec=120,
                                       should_continue=lambda: False,
                                       poll_interval=0.02)
        rapport.egal("un should_continue() faux interrompt l'enregistrement",
                     resultat, "interrupted")
        rapport.verifie("le sous-processus arecord est bien arrêté (aucun orphelin)",
                        popen.processus[0].terminate_appele is True)

        popen = PopenFactice(duree=0.05, returncode=1, stderr=b"arecord: erreur")
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.record(cible, max_duration_sec=120,
                                       should_continue=lambda: True,
                                       poll_interval=0.02)
        rapport.egal("un arecord en échec est signalé", resultat, "error")

        popen = PopenFactice(erreur=OSError("arecord introuvable"))
        with harness.remplacer(audio_io.subprocess, "Popen", popen):
            resultat = audio_io.record(cible, max_duration_sec=120,
                                       should_continue=lambda: True)
        rapport.egal("un arecord absent ne lève pas d'exception", resultat, "error")


def test_reel(rapport: Rapport, secondes: int) -> None:
    """Enregistrement réel sur le téléphone assemblé (§7.6 points 4 et 6).

    Rien n'est réimplémenté ici : ce sont les fonctions du service qui sont
    appelées (audio_io.record, livre_dor.HangupConfirmer,
    timestamped_recording_path, wav_duration_sec, disk_space_state), avec les
    paramètres de l'installation. Seul le dossier de destination est
    temporaire : messages/ n'est jamais touché par un test.
    """
    harness.resume_parametres(rapport, [
        "SOUND_CARD", "MAX_RECORD_SEC", "SHORT_RECORDING_THRESHOLD_SEC",
        "RECORDING_HANGUP_CONFIRM_SEC", "DISK_WARNING_MB", "DISK_CRITICAL_MB",
        "MESSAGES_DIR",
    ])

    rapport.section("1. Prérequis matériels")
    harness.exiger_carte_son(rapport)
    etat_disque = livre_dor.disk_space_state(config.MESSAGES_DIR)
    rapport.egal("l'espace disque autorise un enregistrement (§7.3)", etat_disque, "ok")

    import tempfile
    dossier = Path(tempfile.mkdtemp(prefix="livredor_test_enr_"))
    inputs = None
    try:
        with harness.remplacer(config, "MESSAGES_DIR", dossier):
            cible = livre_dor.timestamped_recording_path()
            rapport.verifie("un nom horodaté est attribué",
                            livre_dor.RECORDING_NAME_RE.match(cible.stem) is not None,
                            f"nom : {cible.name}")

            rapport.section("2. Enregistrement")
            if harness.gpio_disponible():
                # Scénario réel : c'est le crochet qui arrête l'enregistrement,
                # via le même anti-rebond que le service (§7.2).
                inputs = harness.exiger_gpio(rapport)
                harness.attendre_condition(inputs.is_hook_up,
                                            "Décrochez le combiné.")
                confirmeur = livre_dor.HangupConfirmer(inputs)
                print(f"\n  {harness.GRAS}>>> Parlez dans le combiné, puis "
                      f"raccrochez pour arrêter l'enregistrement.{harness.RAZ}")
                arret_attendu = "interrupted"
                should_continue = confirmeur.should_continue
            else:
                rapport.ignore("arrêt de l'enregistrement par le crochet",
                               "RPi.GPIO indisponible")
                print(f"\n  {harness.GRAS}>>> Parlez dans le combiné pendant "
                      f"{secondes} s...{harness.RAZ}")
                debut_fixe = time.monotonic()
                arret_attendu = "interrupted"
                should_continue = lambda: (time.monotonic() - debut_fixe) < secondes

            debut = time.monotonic()
            # max_duration_sec = filet de sécurité de l'installation, comme en
            # service : l'arrêt normal vient de should_continue().
            resultat = audio_io.record(cible, max_duration_sec=config.MAX_RECORD_SEC,
                                        should_continue=should_continue)
            ecoule = time.monotonic() - debut

            rapport.egal("l'enregistrement s'arrête sur commande, sans erreur",
                         resultat, arret_attendu)
            rapport.verifie("le fichier est créé", cible.exists(), f"attendu : {cible}")
            if cible.exists():
                duree = livre_dor.wav_duration_sec(cible)
                taille_ko = cible.stat().st_size / 1024
                rapport.info(f"durée du WAV : {duree:.1f}s — taille : {taille_ko:.0f} Ko "
                             f"— temps écoulé : {ecoule:.1f}s")
                rapport.verifie("le fichier WAV est lisible et non vide",
                                duree > 0.0, "durée nulle : fichier tronqué ou "
                                "arecord interrompu trop tôt")
                rapport.verifie("la durée enregistrée correspond au temps écoulé",
                                abs(duree - ecoule) < 1.5,
                                f"WAV {duree:.1f}s pour {ecoule:.1f}s de prise")
                rapport.verifie("l'enregistrement dépasse le seuil « très court » "
                                f"({config.SHORT_RECORDING_THRESHOLD_SEC}s, §7.2)",
                                duree >= config.SHORT_RECORDING_THRESHOLD_SEC,
                                f"durée : {duree:.1f}s")

                rapport.section("3. Relecture de l'enregistrement")
                print(f"\n  {harness.GRAS}>>> Écoutez : votre message est rejoué."
                      f"{harness.RAZ}")
                relecture = audio_io.play(cible, should_continue=lambda: True,
                                           timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC)
                rapport.egal("l'enregistrement est relisible par aplay", relecture,
                             "completed")
    finally:
        if inputs is not None:
            gpio_io.cleanup()
        shutil.rmtree(dossier, ignore_errors=True)


def main() -> None:
    parser = harness.parseur(__doc__)
    parser.add_argument("--duree", type=int, default=8,
                         help="Durée de l'enregistrement réel en secondes, quand "
                              "aucun crochet n'est câblé pour l'arrêter (--reel).")
    args = parser.parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("ENREGISTREMENT D'UN MESSAGE", "matériel réel" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport, args.duree)
    else:
        test_simule(rapport)
        harness.indice_materiel()
    rapport.conclure()


if __name__ == "__main__":
    main()
