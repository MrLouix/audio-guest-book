#!/usr/bin/env python3
"""Préparation des fichiers audio : mapping des rôles et conversion (§4.2).

Vérifie :

1. la liste des sources proposées par le dashboard (`audio_src/`) ;
2. la précédence mapping explicite > convention de nommage > absent ;
3. le refus des noms de fichier dangereux (traversée de chemin) ;
4. le format produit : 48 kHz, stéréo, 16 bits, **les deux pistes identiques** ;
5. le bip de répondeur (durée, silences) ;
6. qu'une source illisible n'interrompt pas la conversion des autres ;
7. le remplacement atomique du fichier cible ;
8. la commutation des sorties (`alsa_io`) : cache, validation, indisponibilité ;
9. la synchronisation bidirectionnelle de `audio_src/` : dossiers Drive
   disjoints, verrou, bootstrap `--resync` et réparation d'un état perdu.

Les vérifications 4 à 7 demandent `pydub` (et `ffmpeg` pour les sources
compressées) ; elles sont ignorées, et non mises en échec, si la dépendance
manque — `tests/README.md` promet des tests sans installation préalable.

Usage :
    python3 tests/test_audio_prep.py
"""

import wave
from pathlib import Path
from typing import List

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import alsa_io                       # noqa: E402
import audio_config                  # noqa: E402
import config                        # noqa: E402

try:
    from pydub.generators import Sine
    PYDUB_DISPO = True
except ImportError:  # pragma: no cover — dépend de l'environnement
    PYDUB_DISPO = False


def _ecrire_source(nom: str, duree_ms: int = 400, frequence: int = 440,
                   format_: str = "wav") -> Path:
    """Dépose une source dans audio_src/, comme le ferait une synchro Drive."""
    chemin = config.AUDIO_SRC_DIR / nom
    Sine(frequence).to_audio_segment(duration=duree_ms).export(chemin, format=format_)
    return chemin


def _lire_wav(chemin: Path) -> dict:
    """Format et égalité des deux pistes d'un WAV généré."""
    with wave.open(str(chemin), "rb") as fichier:
        canaux = fichier.getnchannels()
        frames = fichier.readframes(fichier.getnframes())
        infos = {
            "canaux": canaux,
            "cadence": fichier.getframerate(),
            "bits": fichier.getsampwidth() * 8,
            "duree_ms": round(fichier.getnframes() * 1000 / fichier.getframerate()),
        }
    if canaux == 2:
        # Comparaison piste à piste sans dépendance externe : les octets pairs
        # portent la gauche, les impairs la droite (16 bits, entrelacés).
        gauche = bytes(b for i, b in enumerate(frames) if (i // 2) % 2 == 0)
        droite = bytes(b for i, b in enumerate(frames) if (i // 2) % 2 == 1)
        infos["pistes_identiques"] = gauche == droite
    else:
        infos["pistes_identiques"] = False
    return infos


def _piste_gauche(chemin: Path) -> List[int]:
    """Échantillons signés de la piste gauche d'un WAV 16 bits."""
    with wave.open(str(chemin), "rb") as fichier:
        canaux = fichier.getnchannels()
        frames = fichier.readframes(fichier.getnframes())
    pas = 2 * canaux
    return [int.from_bytes(frames[i:i + 2], "little", signed=True)
            for i in range(0, len(frames) - pas + 1, pas)]


def _enveloppe(echantillons: List[int], fenetre: int) -> List[int]:
    """Amplitude crête par fenêtre de `fenetre` échantillons.

    C'est ce qui distingue une tonalité continue d'une tonalité modulée : deux
    sinusoïdes voisines battent à leur différence, et leur crête varie d'une
    fenêtre à l'autre. Une seule sinusoïde garde la même crête partout.
    """
    return [max(abs(v) for v in echantillons[i:i + fenetre])
            for i in range(0, len(echantillons) - fenetre + 1, fenetre)]


def test_mapping(rapport: Rapport) -> None:
    rapport.section("1. Sources disponibles et mapping des rôles (§4.2)")
    with Banc(machine=False, chiffres_maries=[]):
        (config.AUDIO_SRC_DIR / "notes.txt").write_text("pas un son", encoding="utf-8")
        (config.AUDIO_SRC_DIR / ".cache.mp3").write_bytes(b"")
        (config.AUDIO_SRC_DIR / "cloches du village.mp3").write_bytes(b"")
        (config.AUDIO_SRC_DIR / "sonnerie.wav").write_bytes(b"")

        sources = audio_config.available_sources()
        rapport.egal("seuls les fichiers audio visibles sont listés",
                     sources, ["cloches du village.mp3", "sonnerie.wav"])

        rapport.egal("sans mapping, le rôle retombe sur la convention de nommage",
                     audio_config.origin_for("sonnerie"), "convention")
        rapport.egal("et c'est bien audio_src/sonnerie.* qui est retenu",
                     audio_config.source_for("sonnerie").name, "sonnerie.wav")

        resultat = audio_config.set_role_sources({"sonnerie": "cloches du village.mp3"})
        rapport.verifie("un fichier au nom libre peut être choisi", resultat["ok"],
                        resultat["erreur"])
        rapport.egal("le mapping explicite prime sur la convention",
                     audio_config.source_for("sonnerie").name, "cloches du village.mp3")
        rapport.egal("l'origine est signalée au dashboard",
                     audio_config.origin_for("sonnerie"), "mapping")

        audio_config.set_role_sources({"sonnerie": ""})
        rapport.egal("effacer le choix ramène à la convention",
                     audio_config.source_for("sonnerie").name, "sonnerie.wav")

        rapport.egal("un rôle sans source du tout est signalé « absent »",
                     audio_config.origin_for("message_4"), "absent")
        rapport.verifie("et n'a pas de source",
                        audio_config.source_for("message_4") is None)

    rapport.section("2. Validation des noms venus du dashboard (§6)")
    with Banc(machine=False, chiffres_maries=[]):
        (config.AUDIO_SRC_DIR / "ok.wav").write_bytes(b"")

        for nom, libelle in [("../../etc/passwd", "une traversée de chemin est refusée"),
                             ("/etc/passwd", "un chemin absolu est refusé"),
                             ("sous/dossier.wav", "un sous-chemin est refusé")]:
            resultat = audio_config.set_role_sources({"sonnerie": nom})
            rapport.verifie(libelle, not resultat["ok"], f"résultat : {resultat}")

        resultat = audio_config.set_role_sources({"sonnerie": "absent.wav"})
        rapport.verifie("un fichier absent d'audio_src/ est refusé", not resultat["ok"],
                        f"résultat : {resultat}")
        resultat = audio_config.set_role_sources({"message_42": "ok.wav"})
        rapport.verifie("un rôle inconnu est refusé", not resultat["ok"],
                        f"résultat : {resultat}")

        # Refus global : une seule valeur invalide ne doit rien écrire du tout.
        audio_config.set_role_sources({"sonnerie": "ok.wav"})
        audio_config.set_role_sources({"message_1": "ok.wav", "message_2": "absent.wav"})
        rapport.verifie("un lot contenant une valeur invalide n'écrit rien",
                        audio_config.read_config()["roles"].get("message_1") is None,
                        f"mapping : {audio_config.read_config()}")

        config.AUDIO_CONFIG_FILE.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
        rapport.egal("un audio_config.json illisible ne lève pas (§7.4)",
                     audio_config.read_config(), {"roles": {}})


def test_conversion(rapport: Rapport) -> None:
    rapport.section("3. Format des fichiers produits (§4.2)")
    if not PYDUB_DISPO:
        rapport.ignore("conversion des sources", "pydub non installé")
        return

    import prepare_audio               # noqa: E402  (dépend de pydub)

    with Banc(machine=False, chiffres_maries=[]):
        _ecrire_source("cloches.wav", duree_ms=500, frequence=523)
        audio_config.set_role_sources({"sonnerie": "cloches.wav"})
        rapport_conv = prepare_audio.prepare_all()
        rapport.verifie("la conversion se termine sans erreur", rapport_conv["ok"],
                        f"erreurs : {rapport_conv['erreurs']}")

        for nom in ("ring_out.wav", "tonalite.wav", "bip.wav"):
            infos = _lire_wav(config.AUDIO_DIR / nom)
            rapport.verifie(
                f"{nom} : 48 kHz, stéréo, 16 bits",
                infos["cadence"] == 48000 and infos["canaux"] == 2 and infos["bits"] == 16,
                f"obtenu : {infos}")
            # Le line out ne lit que la piste gauche, le casque une piste par
            # écouteur : deux pistes différentes rendraient un canal muet.
            rapport.verifie(f"{nom} : les deux pistes sont identiques (L = R)",
                            infos["pistes_identiques"], f"obtenu : {infos}")

        rapport.verifie("aucun panning ne subsiste dans le code de préparation",
                        not hasattr(prepare_audio, "_pan_and_gain"),
                        "prepare_audio._pan_and_gain existe encore")

    rapport.section("4. Tonalité d'invitation à numéroter et bip (§1.2)")
    with Banc(machine=False, chiffres_maries=[]):
        prepare_audio.generate_synthesized()

        # Tonalité d'invitation à numéroter du réseau français : 440 Hz seul.
        # Le mélange 440 + 480 Hz est celui du réseau nord-américain, et son
        # battement à 40 Hz s'entend comme une ondulation — exactement ce que
        # la ligne PTT ne faisait pas.
        rapport.egal("la tonalité est un 440 Hz", prepare_audio.DIAL_TONE_FREQ_HZ, 440)
        echantillons = _piste_gauche(config.TONALITE_WAV)
        # Fenêtre de 5 ms : deux périodes pleines de 440 Hz — assez pour que
        # la crête d'une sinusoïde seule y soit toujours la même, et assez
        # courte pour tomber dans les creux d'un battement à 40 Hz. Mesuré :
        # 0 % d'écart pour un 440 Hz seul, 73 % pour le mélange 440 + 480 Hz.
        fenetre = int(config.AUDIO_RATE_HZ * 0.005)
        # Le silence de tête, lui, est voulu : il absorbe le « pop » de
        # l'activation de l'ampli. Il est écarté de la mesure.
        debut = int(config.AUDIO_RATE_HZ * (prepare_audio.LEAD_SILENCE_MS + 5) / 1000)
        enveloppe = _enveloppe(echantillons[debut:], fenetre)
        creux, crete = min(enveloppe), max(enveloppe)
        rapport.verifie("elle est continue, non modulée (crête constante)",
                        creux > 0 and (crete - creux) / crete < 0.05,
                        f"crête entre {creux} et {crete} sur {len(enveloppe)} fenêtres")
        # Le fichier est rejoué en boucle tant que rien n'est composé : un
        # nombre entier de périodes fait tomber le raccord sur un passage à
        # zéro, sans clic audible.
        periodes = prepare_audio.DIAL_TONE_DURATION_MS * prepare_audio.DIAL_TONE_FREQ_HZ / 1000
        rapport.verifie("sa durée est un nombre entier de périodes (boucle sans clic)",
                        float(periodes).is_integer(), f"{periodes} périodes")

        infos = _lire_wav(config.BIP_WAV)
        attendu = (prepare_audio.BIP_SILENCE_BEFORE_MS + prepare_audio.BIP_DURATION_MS
                   + prepare_audio.BIP_SILENCE_AFTER_MS)
        rapport.verifie(f"le bip dure {attendu} ms (silence + bip + silence)",
                        abs(infos["duree_ms"] - attendu) <= 5,
                        f"durée mesurée : {infos['duree_ms']} ms")
        rapport.verifie("le bip est encadré de silence, pour se détacher du message",
                        prepare_audio.BIP_SILENCE_BEFORE_MS > 0
                        and prepare_audio.BIP_SILENCE_AFTER_MS > 0)
        rapport.verifie("il est plus grave et plus long que l'ancien (1000 Hz / 400 ms)",
                        prepare_audio.BIP_FREQ_HZ < 1000
                        and prepare_audio.BIP_DURATION_MS > 400,
                        f"{prepare_audio.BIP_FREQ_HZ} Hz / {prepare_audio.BIP_DURATION_MS} ms")

    rapport.section("5. Robustesse de la conversion (§7.4)")
    with Banc(machine=False, chiffres_maries=[]):
        _ecrire_source("bon.wav")
        (config.AUDIO_SRC_DIR / "casse.wav").write_bytes(b"ceci n'est pas un WAV")
        audio_config.set_role_sources({"message_generique": "bon.wav",
                                       "sonnerie": "casse.wav"})

        resultat = prepare_audio.prepare_all()
        rapport.verifie("une source illisible est signalée",
                        "sonnerie" in resultat["erreurs"],
                        f"erreurs : {resultat['erreurs']}")
        rapport.verifie("mais n'empêche pas la conversion des autres rôles",
                        config.MESSAGE_GENERIQUE_WAV.exists(),
                        "message_generique.wav n'a pas été généré")

        rapport.verifie("un rôle sans source n'est pas une erreur",
                        "message_5" not in prepare_audio.prepare_all()["erreurs"])

        # Remplacement atomique : à aucun moment un fichier cible ne doit être
        # un WAV tronqué, que livre_dor.py pourrait justement être en train de
        # jouer.
        avant = config.MESSAGE_GENERIQUE_WAV.read_bytes()
        _ecrire_source("bon.wav", duree_ms=900)
        prepare_audio.convert_role("message_generique")
        apres = config.MESSAGE_GENERIQUE_WAV.read_bytes()
        rapport.verifie("la reconversion remplace bien le fichier", avant != apres)
        rapport.verifie("aucun fichier temporaire ne traîne dans audio/",
                        list(config.AUDIO_DIR.glob("*.tmp")) == [],
                        f"restes : {list(config.AUDIO_DIR.glob('*.tmp'))}")
        infos = _lire_wav(config.MESSAGE_GENERIQUE_WAV)
        rapport.verifie("et le fichier reste lisible de bout en bout",
                        infos["cadence"] == 48000 and infos["pistes_identiques"])

    rapport.section("6. Sortie associée à chaque rôle (§4.1)")
    with Banc(machine=False, chiffres_maries=[]):
        rapport.egal("la sonnerie va sur le haut-parleur de sonnerie",
                     prepare_audio.output_for("sonnerie"), config.AUDIO_OUTPUT_SONNERIE)
        rapport.egal("un message des mariés va dans le combiné",
                     prepare_audio.output_for("message_3"), config.AUDIO_OUTPUT_COMBINE)


def test_commutation(rapport: Rapport) -> None:
    rapport.section("7. Commutation des sorties du codec (alsa_io, §4.1)")
    # Banc non utilisé ici : c'est alsa_io lui-même qui est sous test, sans
    # doublure — on remplace seulement le lancement du script.
    alsa_io.invalidate_cache()
    appels = []

    class ResultatFactice:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run_factice(argv, timeout_sec):
        appels.append(list(argv))
        return ResultatFactice(stdout="output=headphone\n")

    with harness.remplacer(alsa_io, "_run", run_factice):
        rapport.verifie("une sortie inconnue est refusée sans rien lancer",
                        alsa_io.select_output("haut-parleur du voisin") is False
                        and appels == [], f"appels : {appels}")

        rapport.verifie("la première commutation est appliquée",
                        alsa_io.select_output("lineout") is True)
        rapport.egal("elle passe par le mode rapide du script",
                     appels[-1], ["switch-lineout"])

        nb = len(appels)
        alsa_io.select_output("lineout")
        rapport.egal("commuter deux fois vers la même sortie ne relance rien",
                     len(appels), nb)
        alsa_io.select_output("lineout", force=True)
        rapport.egal("sauf avec force=True", len(appels), nb + 1)

        alsa_io.select_output("headphone")
        rapport.egal("changer de sortie relance bien le script",
                     appels[-1], ["switch-headphone"])
        rapport.egal("la dernière sortie appliquée est mémorisée",
                     alsa_io.last_output(), "headphone")

        rapport.egal("l'état courant est relu depuis le matériel",
                     alsa_io.current_output(), "headphone")

        alsa_io.setup_card("headphone")
        rapport.egal("la configuration complète n'écrit pas asound.state",
                     appels[-1], ["--no-store", "headphone"])
        alsa_io.setup_card("headphone", store=True)
        rapport.egal("sauf demande explicite (installation, sous root)",
                     appels[-1], ["headphone"])

    # Script en échec : le cache doit être invalidé pour retenter au prochain coup.
    def run_echec(argv, timeout_sec):
        appels.append(list(argv))
        return ResultatFactice(returncode=1, stderr="amixer: cset numid=29 échoué")

    with harness.remplacer(alsa_io, "_run", run_echec):
        rapport.verifie("un script en échec est signalé",
                        alsa_io.select_output("lineout") is False)
        rapport.verifie("et la sortie mémorisée est oubliée (nouvel essai au suivant)",
                        alsa_io.last_output() is None)

    # Environnement sans amixer ni carte : no-op silencieux, jamais d'exception.
    with harness.remplacer(alsa_io, "_run", lambda argv, timeout_sec: None):
        rapport.verifie("sans amixer, la commutation échoue proprement",
                        alsa_io.select_output("lineout") is False)
        rapport.verifie("et la configuration complète aussi",
                        alsa_io.setup_card() is False)

    alsa_io.invalidate_cache()


def test_bisync(rapport: Rapport) -> None:
    """Synchronisation bidirectionnelle de audio_src/ (§5.4)."""
    import rclone_sync                  # noqa: E402

    rapport.section("8. Dossiers Drive des deux jambes (§5.4)")
    with Banc(machine=False, chiffres_maries=[]):
        rclone_sync.ensure_config_exists()

        for enregistrements, sources, libelle in [
            ("Mariage", "Mariage", "un dossier identique est refusé"),
            ("Mariage", "Mariage/sources", "un dossier imbriqué est refusé"),
            ("Mariage/x", "Mariage", "l'imbrication inverse aussi"),
            ("Mariage", "", "un dossier vide est refusé"),
        ]:
            resultat = rclone_sync.update_config("gdrive", enregistrements, 5, True,
                                                  sources_dossier=sources, sources_actif=True)
            rapport.verifie(libelle, not resultat["ok"], f"résultat : {resultat}")

        resultat = rclone_sync.update_config("gdrive", "Mariage", 5, True,
                                              sources_dossier="MariageSources",
                                              sources_actif=True)
        rapport.verifie("deux dossiers disjoints sont acceptés", resultat["ok"],
                        resultat["erreur"])

        rapport.verifie("changer de dossier Drive invalide l'état bisync",
                        _resync_a_refaire(rclone_sync))

    rapport.section("9. Commande bisync et garde-fous (§5.4)")
    with Banc(machine=False, chiffres_maries=[]):
        cfg = dict(rclone_sync.DEFAULT_CONFIG, sources_dossier="MariageSources")

        with harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 70, 0)):
            cmd, avertissements = rclone_sync._bisync_command(cfg, resync=True)
            rapport.verifie("le premier resync garde le plus récent des deux côtés",
                            "--resync-mode" in cmd and cmd[cmd.index("--resync-mode") + 1] == "newer",
                            f"commande : {cmd}")
            rapport.verifie("les conflits sont résolus sans rien perdre",
                            "--conflict-resolve" in cmd and "--conflict-loser" in cmd,
                            f"commande : {cmd}")
            rapport.egal("une suppression massive est bloquée (% de fichiers)",
                         cmd[cmd.index("--max-delete") + 1],
                         str(config.RCLONE_BISYNC_MAX_DELETE_PCT))
            rapport.egal("aucun avertissement sur une version récente", avertissements, [])

        with harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 60, 0)):
            cmd, avertissements = rclone_sync._bisync_command(cfg, resync=False)
            rapport.verifie("sur rclone ancien, les options récentes sont retirées",
                            "--conflict-resolve" not in cmd, f"commande : {cmd}")
            rapport.verifie("et la dégradation est signalée", len(avertissements) == 1,
                            f"avertissements : {avertissements}")

        rapport.verifie("deux bisync simultanés sont impossibles (verrou)",
                        _verrou_exclusif(rclone_sync))

    rapport.section("10. Bootstrap et réparation de l'état bisync (§5.4)")
    with Banc(machine=False, chiffres_maries=[]):
        appels = []

        def run_ok(cmd, **kwargs):
            appels.append(list(cmd))
            return _ResultatRclone(0)

        with harness.remplacer(rclone_sync.shutil, "which", lambda nom: "/usr/bin/rclone"), \
             harness.remplacer(rclone_sync.subprocess, "run", run_ok), \
             harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 60, 0)):
            rclone_sync.write_config(dict(rclone_sync.DEFAULT_CONFIG, sources_actif=True))
            resultat = rclone_sync.run_bisync_sources()
            # Sans --resync-mode, un resync automatique prendrait le Pi comme
            # référence et effacerait du Drive ce qui n'est pas encore descendu.
            rapport.verifie("sur rclone ancien, aucun resync n'est lancé tout seul",
                            not resultat["ok"] and appels == [], f"appels : {appels}")
            resultat = rclone_sync.run_bisync_sources(resync=True)
            rapport.verifie("mais une demande explicite reste possible",
                            resultat["ok"] and "--resync" in appels[-1], f"appels : {appels}")

        appels.clear()
        with harness.remplacer(rclone_sync.shutil, "which", lambda nom: "/usr/bin/rclone"), \
             harness.remplacer(rclone_sync.subprocess, "run", run_ok), \
             harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 70, 0)):
            rclone_sync.write_config(dict(rclone_sync.DEFAULT_CONFIG, sources_actif=True))
            rclone_sync.run_bisync_sources()
            rapport.verifie("sur rclone récent, le premier cycle initialise tout seul",
                            "--resync" in appels[-1], f"appels : {appels}")
            rapport.verifie("le témoin est posé après succès",
                            rclone_sync.read_config()["sources_resync_fait"] is True)
            rclone_sync.run_bisync_sources()
            rapport.verifie("les cycles suivants n'en refont pas",
                            "--resync" not in appels[-1], f"appels : {appels}")

        essais = []

        def run_etat_perdu(cmd, **kwargs):
            essais.append(list(cmd))
            return _ResultatRclone(
                1, stderr="Bisync critical error: cannot find prior listing, must run --resync")

        with harness.remplacer(rclone_sync.shutil, "which", lambda nom: "/usr/bin/rclone"), \
             harness.remplacer(rclone_sync.subprocess, "run", run_etat_perdu), \
             harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 70, 0)):
            rclone_sync.run_bisync_sources()
            rapport.egal("un état perdu déclenche une seule nouvelle tentative",
                         len(essais), 2)
            rapport.verifie("la seconde tentative réinitialise l'état",
                            "--resync" in essais[-1], f"essais : {essais}")
            rapport.verifie("et le témoin repasse à faux pour le cycle suivant",
                            rclone_sync.read_config()["sources_resync_fait"] is False)

        # Désactivée : aucun appel à rclone.
        muet = []
        with harness.remplacer(rclone_sync.shutil, "which", lambda nom: "/usr/bin/rclone"), \
             harness.remplacer(rclone_sync.subprocess, "run",
                               lambda cmd, **kw: muet.append(cmd) or _ResultatRclone(0)):
            rclone_sync.write_config(dict(rclone_sync.DEFAULT_CONFIG, sources_actif=False))
            resultat = rclone_sync.run_bisync_sources()
            rapport.verifie("désactivée, la jambe bidirectionnelle ne lance rien",
                            resultat.get("skipped") and muet == [], f"appels : {muet}")

    rapport.section("11. Un cycle complet enchaîne les deux jambes (§5.4)")
    with Banc(machine=False, chiffres_maries=[]):
        rclone_sync.write_config(dict(rclone_sync.DEFAULT_CONFIG, sources_actif=True,
                                       sources_resync_fait=True))
        appels = []
        with harness.remplacer(rclone_sync.shutil, "which", lambda nom: "/usr/bin/rclone"), \
             harness.remplacer(rclone_sync.subprocess, "run",
                               lambda cmd, **kw: appels.append(list(cmd)) or _ResultatRclone(0)), \
             harness.remplacer(rclone_sync, "rclone_version", lambda: (1, 70, 0)):
            resultat = rclone_sync.run_sync()

        sous_commandes = [c[1] for c in appels]
        rapport.egal("les enregistrements partent d'abord, en copie montante",
                     sous_commandes, ["copy", "bisync"])
        rapport.verifie("messages/ n'est jamais synchronisé dans les deux sens",
                        "sync" not in sous_commandes and str(config.MESSAGES_DIR) not in appels[1],
                        f"commandes : {appels}")
        rapport.verifie("le cycle complet est signalé réussi", resultat["ok"],
                        f"résultat : {resultat}")


class _ResultatRclone:
    """Imite subprocess.CompletedProcess pour les appels à rclone."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _resync_a_refaire(rclone_sync) -> bool:
    """Changer de dossier Drive doit invalider l'état de référence de rclone."""
    rclone_sync.write_config(dict(rclone_sync.DEFAULT_CONFIG,
                                   sources_dossier="Avant", sources_resync_fait=True))
    rclone_sync.update_config("gdrive", "Mariage", 5, True,
                               sources_dossier="Apres", sources_actif=True)
    return rclone_sync.read_config()["sources_resync_fait"] is False


def _verrou_exclusif(rclone_sync) -> bool:
    """Un second bisync ne doit pas pouvoir démarrer pendant le premier."""
    with rclone_sync._bisync_lock() as premier:
        with rclone_sync._bisync_lock() as second:
            return premier is True and second is False


def main() -> None:
    rapport = Rapport("PRÉPARATION AUDIO",
                      "mapping des rôles, conversion 48 kHz, sorties du codec, synchro bidirectionnelle")
    test_mapping(rapport)
    test_conversion(rapport)
    test_commutation(rapport)
    test_bisync(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
