#!/usr/bin/env python3
"""Test unitaire de la BASCULE MARIAGE / RESTITUTION (§5.2, §5.7, §7.4).

Le téléphone a deux vies : pendant l'événement il enregistre les invités
(mode mariage), après l'événement il relit leurs messages (mode restitution).
La bascule se fait depuis le dashboard, à chaud, sans SSH ni redémarrage du
service — c'est mode_config.json qui fait le lien entre les deux processus.

Ce que vérifie ce script, seul et sans matériel :

1. le contrat de mode_config.json et son écriture atomique ;
2. la tolérance à un fichier absent, vide, corrompu ou mal formé (§7.4) ;
3. le cache de MODE_RELOAD_SEC et son invalidation à l'écriture ;
4. la prise en compte à chaud par la machine à états ;
5. l'invariant du mode restitution : aucun enregistrement, jamais (§5.7).

Usage :
    python3 tests/test_mode.py
"""

import json
import time

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import config                        # noqa: E402
import livre_dor                     # noqa: E402
import mode_io                       # noqa: E402


def test_simule(rapport: Rapport) -> None:
    rapport.section("1. Contrat de mode_config.json (§8)")
    with Banc(machine=False) as banc:
        mode_io.write_mode(True)
        contenu = json.loads(config.MODE_CONFIG_FILE.read_text(encoding="utf-8"))
        rapport.egal("le fichier contient bien { \"restitution\": true }",
                     contenu, {"restitution": True})
        rapport.verifie("is_restitution() suit le fichier",
                        mode_io.is_restitution(force=True) is True)
        rapport.egal("le libellé du mode est « restitution »",
                     mode_io.mode_label(), "restitution")

        mode_io.write_mode(False)
        rapport.verifie("la bascule inverse fonctionne",
                        mode_io.is_restitution(force=True) is False)
        rapport.egal("le libellé du mode est « mariage »", mode_io.mode_label(), "mariage")
        rapport.verifie("aucun fichier temporaire ne subsiste (écriture atomique)",
                        list(config.MODE_CONFIG_FILE.parent.glob("*.tmp")) == [],
                        f"restes : {list(config.MODE_CONFIG_FILE.parent.glob('*.tmp'))}")

    rapport.section("2. Tolérance aux fichiers douteux (§7.4)")
    with Banc(machine=False) as banc:
        config.MODE_CONFIG_FILE.unlink()
        mode_io.invalidate_cache()
        rapport.egal("fichier absent -> valeurs par défaut",
                     mode_io.read_mode(force=True), mode_io.DEFAULT_CONFIG)

        config.MODE_CONFIG_FILE.write_text("{ceci n'est pas du JSON", encoding="utf-8")
        mode_io.invalidate_cache()
        rapport.egal("fichier corrompu -> valeurs par défaut",
                     mode_io.read_mode(force=True), mode_io.DEFAULT_CONFIG)

        config.MODE_CONFIG_FILE.write_text("[1, 2, 3]", encoding="utf-8")
        mode_io.invalidate_cache()
        rapport.egal("JSON qui n'est pas un objet -> valeurs par défaut",
                     mode_io.read_mode(force=True), mode_io.DEFAULT_CONFIG)

        config.MODE_CONFIG_FILE.write_text('{"autre_cle": 1}', encoding="utf-8")
        mode_io.invalidate_cache()
        rapport.verifie("une clé inconnue ne fait pas disparaître « restitution »",
                        "restitution" in mode_io.read_mode(force=True))

        config.MODE_CONFIG_FILE.unlink()
        mode_io.ensure_config_exists()
        rapport.verifie("ensure_config_exists() recrée le fichier manquant",
                        config.MODE_CONFIG_FILE.exists())

        mode_io.write_mode(True)
        lu = mode_io.read_mode()
        lu["restitution"] = "altéré par l'appelant"
        rapport.verifie("read_mode() renvoie une copie (le cache reste intact)",
                        mode_io.read_mode()["restitution"] is True,
                        f"valeur en cache : {mode_io.read_mode()['restitution']!r}")

    rapport.section("3. Cache de relecture (MODE_RELOAD_SEC)")
    # La machine à états interroge le mode 20 fois par seconde : le fichier ne
    # doit pas être relu à cette cadence sur une carte SD.
    with Banc(machine=False, MODE_RELOAD_SEC=0.5) as banc:
        lectures = []
        lecture_reelle = mode_io._read_file
        with harness.remplacer(mode_io, "_read_file",
                                lambda: (lectures.append(1), lecture_reelle())[1]):
            mode_io.write_mode(False)
            lectures.clear()
            for _ in range(40):                     # deux secondes de boucle à 20 Hz
                mode_io.is_restitution()
            rapport.egal("40 appels rapprochés ne relisent le fichier qu'une fois",
                         len(lectures), 1)

            lectures.clear()
            mode_io.is_restitution(force=True)
            rapport.egal("force=True court-circuite le cache", len(lectures), 1)

            # Bascule faite par ce processus (le dashboard) : visible aussitôt.
            mode_io.write_mode(True)
            rapport.verifie("une bascule locale est vue immédiatement",
                            mode_io.is_restitution() is True)

            # Bascule faite par un autre processus : vue après expiration du TTL.
            config.MODE_CONFIG_FILE.write_text('{"restitution": false}', encoding="utf-8")
            rapport.verifie("la valeur reste en cache juste après",
                            mode_io.is_restitution() is True)
            time.sleep(config.MODE_RELOAD_SEC + 0.15)
            rapport.verifie("elle est relue après expiration du cache",
                            mode_io.is_restitution() is False)

    rapport.section("4. Prise en compte à chaud par la machine à états (§5.7)")
    with Banc(restitution=False, messages=3, RESTITUTION_INTERDIGIT_SEC=0.4) as banc:
        mode_io.write_mode(True)                    # bascule depuis le dashboard
        time.sleep(config.MODE_RELOAD_SEC + 0.2)
        banc.decrocher()
        banc.composer(1)
        time.sleep(0.4 + 3 * harness.STABILISATION_SEC)
        rapport.egal("le décroché suivant ouvre le parcours de restitution",
                     banc.messages_invites_lus(), [banc.noms_messages[0]])
        rapport.egal("aucun enregistrement n'est créé", banc.enregistrements_crees(), [])
        banc.raccrocher()

        mode_io.write_mode(False)                   # retour en mode mariage
        time.sleep(config.MODE_RELOAD_SEC + 0.2)
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        banc.composer(3)
        rapport.verifie("le décroché suivant redevient le parcours mariage",
                        banc.attendre_lecture("message_3.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")

    rapport.section("5. Une communication engagée n'est jamais coupée (§5.7)")
    with Banc(restitution=False) as banc:
        banc.decrocher()
        banc.attendre_etat(livre_dor.STATE_NUMEROTATION)
        mode_io.write_mode(True)                    # bascule pendant l'appel
        banc.composer(3)
        rapport.verifie("l'appel en cours continue en mode mariage",
                        banc.attendre_lecture("message_3.wav"),
                        f"fichiers joués : {banc.audio.noms_lus()}")
        banc.attendre_etat(livre_dor.STATE_ENREGISTREMENT, timeout=5.0)
        time.sleep(0.4)
        rapport.egal("mais l'enregistrement est refusé par le garde-fou",
                     banc.audio.noms_enregistres(), [])
        rapport.egal("et aucun fichier n'apparaît dans messages/",
                     banc.enregistrements_crees(), [])

    rapport.section("6. Le mode courant est visible depuis le dashboard (§5.2)")
    with harness.journal_status() as journal, Banc(restitution=True) as banc:
        time.sleep(0.3)
        rapport.verifie("status.json signale le mode restitution en attente",
                        (livre_dor.STATE_ATTENTE, "mode restitution") in journal,
                        f"états publiés : {journal[:5]}")
    with harness.journal_status() as journal, Banc(restitution=False) as banc:
        time.sleep(0.3)
        rapport.verifie("et ne signale rien de particulier en mode mariage",
                        (livre_dor.STATE_ATTENTE, None) in journal,
                        f"états publiés : {journal[:5]}")


def main() -> None:
    args = harness.parseur(__doc__, reel=False).parse_args()
    harness.configurer_logs(args.verbeux)
    rapport = Rapport("MODE MARIAGE / RESTITUTION", "simulation, aucun matériel requis")
    test_simule(rapport)
    rapport.conclure()


if __name__ == "__main__":
    main()
