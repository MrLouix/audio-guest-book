#!/usr/bin/env python3
"""Test unitaire de la PUBLICATION D'ÉTAT, des JOURNAUX et du WATCHDOG (§7.1, §7.2, §7.3, §8).

status.json est le seul lien entre la machine à états et le reste du système :
le dashboard y lit ce que fait le téléphone (§5.2), et le watchdog y lit s'il
est toujours vivant (§7.1). Un fichier à moitié écrit ou périmé provoquerait,
au choix, un affichage cassé ou un redémarrage intempestif du service en
plein événement.

Ce que vérifie ce script, seul et sans matériel :

1. le contrat du fichier et son écriture atomique ;
2. la tolérance à un fichier absent ou corrompu (§7.4) ;
3. le battement de cœur en attente et combiné décroché — sans lui, le
   watchdog redémarrerait le service en boucle (§7.1) ;
4. la décision du watchdog : service en échec, status.json périmé, ou rien ;
5. les journaux de mise en service (§7.3) : niveau réglable, issue de chaque
   lecture audio, messages d'ALSA, silence de la tonalité et détail du cadran.
   Ce sont les seules traces disponibles lors des premiers essais sur le
   téléphone, où rien n'est observable autrement.

Usage :
    python3 tests/test_status.py          # simulation, aucune dépendance
    python3 tests/test_status.py --reel   # état réel du service installé
"""

import datetime
import json
import logging
import subprocess
import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, PopenFactice, Rapport

import audio_io                      # noqa: E402
import config                        # noqa: E402
import livre_dor                     # noqa: E402
import status_io                     # noqa: E402
import watchdog                      # noqa: E402


class SystemctlFactice:
    """Remplaçant de subprocess.run : journalise les appels à systemctl."""

    def __init__(self, en_echec=()) -> None:
        self.appels = []
        self.en_echec = set(en_echec)

    def __call__(self, cmd, **kwargs):
        self.appels.append(list(cmd))
        resultat = subprocess.CompletedProcess(cmd, 0)
        if list(cmd[:2]) == ["systemctl", "is-failed"]:
            resultat.returncode = 0 if cmd[-1] in self.en_echec else 1
        return resultat

    def redemarrages(self):
        return [cmd[-1] for cmd in self.appels if "restart" in cmd]


def _ecrire_status_daté(etat: str, age_sec: float, detail: str = "") -> None:
    """Écrit un status.json dont l'horodatage remonte à `age_sec` secondes."""
    instant = datetime.datetime.now() - datetime.timedelta(seconds=age_sec)
    config.STATUS_FILE.write_text(json.dumps({
        "etat": etat,
        "derniere_maj": instant.isoformat(timespec="seconds"),
        "detail": detail,
    }, ensure_ascii=False), encoding="utf-8")


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Contrat de status.json (§8)")
    with Banc(machine=False) as banc:
        status_io.write_status(livre_dor.STATE_ENREGISTREMENT, detail="essai")
        donnees = json.loads(config.STATUS_FILE.read_text(encoding="utf-8"))
        rapport.egal("les trois clés du contrat sont présentes",
                     sorted(donnees), ["derniere_maj", "detail", "etat"])
        rapport.egal("l'état est celui demandé", donnees["etat"], "enregistrement")
        rapport.egal("le détail est celui demandé", donnees["detail"], "essai")
        try:
            datetime.datetime.fromisoformat(donnees["derniere_maj"])
            horodatage_valide = True
        except ValueError:
            horodatage_valide = False
        rapport.verifie("l'horodatage est en ISO-8601", horodatage_valide,
                        f"horodatage : {donnees['derniere_maj']}")

        status_io.write_status(livre_dor.STATE_ATTENTE)
        rapport.egal("sans détail, le champ est une chaîne vide (jamais absent)",
                     status_io.read_status()["detail"], "")
        rapport.verifie("aucun fichier temporaire ne subsiste (écriture atomique)",
                        list(config.STATUS_FILE.parent.glob("*.tmp")) == [],
                        f"restes : {list(config.STATUS_FILE.parent.glob('*.tmp'))}")
        rapport.egal("read_status() relit ce que write_status() a écrit",
                     status_io.read_status()["etat"], "attente")

    rapport.section("2. Tolérance aux fichiers douteux (§7.4)")
    with Banc(machine=False) as banc:
        config.STATUS_FILE.unlink(missing_ok=True)
        rapport.verifie("fichier absent -> None, sans exception",
                        status_io.read_status() is None)
        config.STATUS_FILE.write_text("{tronqué", encoding="utf-8")
        rapport.verifie("fichier corrompu -> None, sans exception",
                        status_io.read_status() is None)

    rapport.section("3. Battement de cœur en attente (§7.1)")
    # Sans battement, status.json resterait figé pendant les longues périodes
    # sans invité et le watchdog redémarrerait le service.
    with harness.journal_status() as journal, Banc(STATUS_HEARTBEAT_SEC=0.3) as banc:
        time.sleep(0.2)
        journal.clear()
        time.sleep(1.2)
        battements = [e for e in journal if e == (livre_dor.STATE_ATTENTE, None)]
        rapport.verifie("status.json est rafraîchi périodiquement en attente",
                        len(battements) >= 2,
                        f"{len(battements)} battement(s) en 1,2 s")
        rapport.verifie("le battement respecte l'intervalle configuré "
                        "(pas d'écriture à 20 Hz sur la carte SD)",
                        len(battements) <= 6, f"{len(battements)} battements en 1,2 s")

    rapport.section("4. Battement de cœur combiné laissé décroché (§5.7, §7.1)")
    # Le seul état du parcours dont la durée n'est pas bornée : un combiné posé
    # à côté du téléphone y reste indéfiniment.
    with harness.journal_status() as journal, Banc(restitution=True, messages=3,
                                                    STATUS_HEARTBEAT_SEC=0.3,
                                                    RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        banc.decrocher()
        banc.composer(2)
        banc.attendre_etat(livre_dor.STATE_RESTITUTION_LECTURE, timeout=3.0)
        time.sleep(0.5)
        journal.clear()
        time.sleep(1.2)                        # combiné toujours décroché
        battements = [e for e in journal
                      if e == (livre_dor.STATE_RESTITUTION_LECTURE, "attente du raccroché")]
        rapport.verifie("status.json continue de battre pendant l'attente du raccroché",
                        len(battements) >= 2,
                        f"{len(battements)} battement(s) en 1,2 s")

    rapport.section("5. Âge de status.json (watchdog._status_age_seconds)")
    with Banc(machine=False) as banc:
        _ecrire_status_daté(livre_dor.STATE_ATTENTE, age_sec=0)
        age = watchdog._status_age_seconds()
        rapport.verifie("un status.json frais a un âge quasi nul",
                        age is not None and age < 2.0, f"âge : {age}")
        _ecrire_status_daté(livre_dor.STATE_ATTENTE, age_sec=600)
        age = watchdog._status_age_seconds()
        rapport.verifie("un status.json ancien est vu périmé",
                        age is not None and age >= 595, f"âge : {age}")
        config.STATUS_FILE.write_text('{"etat": "attente", "derniere_maj": "hier"}',
                                       encoding="utf-8")
        rapport.verifie("un horodatage illisible ne lève pas d'exception",
                        watchdog._status_age_seconds() is None)
        config.STATUS_FILE.unlink()
        rapport.verifie("un status.json absent ne lève pas d'exception",
                        watchdog._status_age_seconds() is None)

    rapport.section("6. Décision du watchdog (§7.1)")
    with Banc(machine=False, WATCHDOG_STALE_AFTER_SEC=300) as banc:
        systemctl = SystemctlFactice()
        _ecrire_status_daté(livre_dor.STATE_ATTENTE, age_sec=5)
        with harness.remplacer(watchdog.subprocess, "run", systemctl):
            watchdog.check_and_restart_if_needed()
        rapport.egal("service sain et status.json frais : aucun redémarrage",
                     systemctl.redemarrages(), [])

        systemctl = SystemctlFactice()
        _ecrire_status_daté(livre_dor.STATE_ATTENTE, age_sec=400)
        with harness.remplacer(watchdog.subprocess, "run", systemctl):
            watchdog.check_and_restart_if_needed()
        rapport.egal("status.json périmé : le service est redémarré",
                     systemctl.redemarrages(), ["livre-dor.service"])

        systemctl = SystemctlFactice(en_echec=["dashboard.service"])
        _ecrire_status_daté(livre_dor.STATE_ATTENTE, age_sec=5)
        with harness.remplacer(watchdog.subprocess, "run", systemctl):
            watchdog.check_and_restart_if_needed()
        rapport.egal("un service en échec est relancé, même isolément",
                     systemctl.redemarrages(), ["dashboard.service"])
        rapport.verifie("le compteur d'échecs est remis à zéro avant le redémarrage",
                        any("reset-failed" in cmd for cmd in systemctl.appels),
                        f"appels : {systemctl.appels}")

        systemctl = SystemctlFactice()
        config.STATUS_FILE.unlink(missing_ok=True)
        with harness.remplacer(watchdog.subprocess, "run", systemctl):
            watchdog.check_and_restart_if_needed()
        rapport.egal("status.json absent (service en cours de démarrage) : on patiente",
                     systemctl.redemarrages(), [])

        rapport.verifie("tous les services longs sont surveillés (§7.1)",
                        set(watchdog.MANAGED_SERVICES) == {
                            "livre-dor.service", "dashboard.service",
                            "wifi-or-ap.service", "rclone-sync.service"},
                        f"surveillés : {watchdog.MANAGED_SERVICES}")


    rapport.section("7. Niveau de journalisation réglable (§7.3)")
    # Sans réglage, tout le détail du cadran reste en DEBUG donc injoignable :
    # il fallait éditer livre_dor.py sur le Pi pour deverminer un chiffre mal
    # compté.
    rapport.egal("« DEBUG » est reconnu", livre_dor.niveau_log("DEBUG"), logging.DEBUG)
    rapport.egal("la casse est indifférente", livre_dor.niveau_log("debug"), logging.DEBUG)
    rapport.egal("« INFO » reste le mode d'exploitation",
                 livre_dor.niveau_log("INFO"), logging.INFO)
    rapport.egal("une valeur incomprise retombe sur INFO sans lever (§7.4)",
                 livre_dor.niveau_log("peut-être"), logging.INFO)
    rapport.verifie("le niveau est réglable depuis le dashboard",
                    config.MODIFIABLE_PARAMS.get("LOG_LEVEL", {}).get("choices")
                    == ["INFO", "DEBUG"],
                    f"paramètre : {config.MODIFIABLE_PARAMS.get('LOG_LEVEL')}")

    with Banc(machine=False, LOG_LEVEL="DEBUG") as banc:
        racine = logging.getLogger()
        handlers, niveau = list(racine.handlers), racine.level
        try:
            livre_dor.setup_logging()
            rapport.egal("setup_logging() suit LOG_LEVEL", racine.level, logging.DEBUG)
            livre_dor.setup_logging(logging.INFO)
            rapport.egal("et un niveau explicite (--verbeux) l'emporte",
                         racine.level, logging.INFO)
        finally:
            racine.handlers[:] = handlers
            racine.setLevel(niveau)

    rapport.section("8. Issue de chaque lecture audio (§7.3)")
    # audio_io.play() ne rend son résultat qu'à son appelant, qui n'en fait
    # rien : sans cette trace, le journal ne dit pas si la tonalité a été
    # coupée par une impulsion, par un raccroché, ou si elle est allée au bout.
    with Banc(machine=False, doublure_audio=False) as banc:
        cible = config.MESSAGE_GENERIQUE_WAV

        popen = PopenFactice(duree=60.0)
        with harness.journal_logs(nom="audio_io") as journal:
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.play(cible, should_continue=lambda: False, poll_interval=0.02)
        rapport.verifie("une lecture interrompue est journalisée avec son issue",
                        any("message_generique.wav" in m and "interrupted" in m
                            for m in journal),
                        f"journal : {journal}")

        popen = PopenFactice(duree=0.1)
        with harness.journal_logs(nom="audio_io") as journal:
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.play(cible, should_continue=lambda: True, poll_interval=0.02)
        rapport.verifie("une lecture menée à terme aussi, avec sa durée",
                        any("completed" in m and " s" in m for m in journal),
                        f"journal : {journal}")

        # Le cas qui était perdu : stderr n'était lu que sur un code de retour
        # non nul, donc jamais sur une interruption — soit le cas courant du
        # parcours, et celui où ALSA signale un périphérique occupé.
        popen = PopenFactice(duree=60.0, stderr=b"aplay: device or resource busy")
        with harness.journal_logs(nom="audio_io") as journal:
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.play(cible, should_continue=lambda: False, poll_interval=0.02)
        rapport.verifie("ce qu'ALSA écrit sur une lecture interrompue est repris",
                        any("device or resource busy" in m for m in journal),
                        f"journal : {journal}")

        popen = PopenFactice(duree=60.0, stderr=b"arecord: device busy")
        with harness.journal_logs(nom="audio_io") as journal:
            with harness.remplacer(audio_io.subprocess, "Popen", popen):
                audio_io.record(config.MESSAGES_DIR / "essai.wav", max_duration_sec=5,
                                should_continue=lambda: False, poll_interval=0.02)
        rapport.verifie("et sur un enregistrement interrompu de même",
                        any("device busy" in m for m in journal),
                        f"journal : {journal}")

    rapport.section("9. Silence de la tonalité et détail du cadran (§7.3, §1.2)")
    # Passé TONALITE_MAX_SEC la ligne devient muette : sans trace, le silence
    # ressemble à une panne de carte son.
    with Banc(machine=False, TONALITE_MAX_SEC=0) as banc:
        banc.inputs.set_hook(True)
        tonalite = livre_dor.Tonalite(banc.inputs)
        with harness.journal_logs(nom="livre_dor") as journal:
            tonalite.tenir()
            tonalite.tenir()
        silences = [m for m in journal if "TONALITE_MAX_SEC" in m]
        rapport.egal("l'extinction de la tonalité est journalisée une seule fois",
                     len(silences), 1)
        rapport.egal("et aucune lecture n'est lancée pour autant",
                     banc.audio.noms_lus(), [])

    with Banc(machine=False) as banc:
        with harness.journal_logs(nom="gpio_io") as journal:
            banc.composer(3)
        rapport.verifie("en DEBUG, le chiffre validé et ses impulsions sont tracés",
                        any("chiffre validé : 3" in m for m in journal),
                        f"journal : {journal}")
        with harness.journal_logs(nom="gpio_io") as journal:
            banc.inputs.register_pulse()        # cadran au repos
        rapport.verifie("une impulsion parasite hors rotation est tracée",
                        any("impulsion ignorée" in m for m in journal),
                        f"journal : {journal}")


def test_reel(rapport: Rapport) -> None:
    """État réel publié par le service installé, sans rien modifier."""
    harness.resume_parametres(rapport, [
        "STATUS_FILE", "STATUS_HEARTBEAT_SEC", "WATCHDOG_STALE_AFTER_SEC",
    ])

    rapport.section("1. status.json de l'installation")
    donnees = status_io.read_status()
    if donnees is None:
        rapport.verifie("status.json est lisible", False,
                        f"{config.STATUS_FILE} absent ou illisible — le service "
                        "livre-dor.service tourne-t-il ?")
        return
    rapport.info(f"état      : {donnees.get('etat')}")
    rapport.info(f"détail    : {donnees.get('detail') or '(aucun)'}")
    rapport.info(f"mise à jour : {donnees.get('derniere_maj')}")
    rapport.verifie("l'état publié est un état connu de la machine",
                    donnees.get("etat") in {
                        livre_dor.STATE_ATTENTE, livre_dor.STATE_SONNERIE,
                        livre_dor.STATE_DECROCHE, livre_dor.STATE_NUMEROTATION,
                        livre_dor.STATE_LECTURE_MESSAGE, livre_dor.STATE_APPEL_REPONDU,
                        livre_dor.STATE_ENREGISTREMENT, livre_dor.STATE_ERREUR,
                        livre_dor.STATE_RESTITUTION_NUMEROTATION,
                        livre_dor.STATE_RESTITUTION_LECTURE},
                    f"état inconnu : {donnees.get('etat')!r}")
    rapport.verifie("le service ne signale pas d'erreur",
                    donnees.get("etat") != livre_dor.STATE_ERREUR,
                    f"détail de l'erreur : {donnees.get('detail')}")

    rapport.section("2. Fraîcheur vue par le watchdog (§7.1)")
    age = watchdog._status_age_seconds()
    if age is None:
        rapport.verifie("l'horodatage est exploitable", False,
                        "derniere_maj absent ou illisible")
        return
    rapport.info(f"âge de status.json : {age:.0f}s "
                 f"(seuil de redémarrage : {config.WATCHDOG_STALE_AFTER_SEC}s)")
    rapport.verifie("status.json n'est pas périmé pour le watchdog",
                    age < config.WATCHDOG_STALE_AFTER_SEC,
                    f"{age:.0f}s écoulées : le watchdog va redémarrer "
                    "livre-dor.service")
    rapport.verifie("le battement de cœur fonctionne",
                    age < max(config.STATUS_HEARTBEAT_SEC * 2, 10),
                    f"{age:.0f}s sans mise à jour pour un battement attendu "
                    f"toutes les {config.STATUS_HEARTBEAT_SEC}s")


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("ÉTAT ET WATCHDOG", "service installé" if args.reel
                      else "simulation, aucun matériel requis")
    if args.reel:
        test_reel(rapport)
    else:
        test_simule(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
