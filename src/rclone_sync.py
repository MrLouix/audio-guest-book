"""Synchronisation Google Drive via rclone (§5.4).

Deux jambes, aux règles volontairement différentes :

1. **`messages/` -> Drive, montant seul** (`rclone copy`, jamais `sync`) : ne
   fait qu'ajouter les fichiers nouveaux ou modifiés, ne supprime jamais rien
   ni en local ni sur le Drive. Les enregistrements des invités sont le
   livrable du mariage : aucune action côté Drive ne doit pouvoir les effacer.
2. **`audio_src/` <-> Drive, bidirectionnel** (`rclone bisync`) : permet de
   déposer une sonnerie ou un message des mariés depuis un smartphone et de
   le retrouver sur le Pi (et inversement). Chaque jambe a son propre dossier
   Drive — les confondre rendrait les enregistrements bidirectionnels, ce que
   la règle 1 interdit.

Configuration éditable depuis le dashboard (rclone_config.json, contrat du
§8), journalisée dans logs/rclone.log dans notre propre format (facile à
relire pour l'indicateur de statut du dashboard, plutôt que de dépendre du
format interne de rclone).

Peut aussi s'exécuter en CLI (`python3 src/rclone_sync.py --run`), ce que
fait le service systemd rclone-sync.service à chaque déclenchement du timer.
"""

import argparse
import contextlib
import datetime
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "remote": config.RCLONE_REMOTE,
    "dossier": config.RCLONE_FOLDER,
    "intervalle_min": config.RCLONE_INTERVAL_MIN,
    "actif": True,
    # Jambe bidirectionnelle (audio_src/). Inactive par défaut : une
    # installation existante ne change pas de comportement tant que
    # l'utilisateur ne l'a pas activée depuis /rclone, et le premier
    # `--resync` — qui peut transférer beaucoup — reste un acte délibéré.
    "sources_actif": False,
    "sources_dossier": config.RCLONE_SOURCES_FOLDER,
    "sources_resync_fait": False,
}

LOG_TAIL_LINES = 200
RECENT_ERRORS_COUNT = 5

LOCK_FILE_NAME = ".bisync.lock"

# Messages de rclone signifiant « l'état bisync est perdu ou absent ».
BISYNC_RESYNC_REQUIS = (
    "must run --resync",
    "cannot find prior listing",
    "bisync critical error",
    "path1 and path2 are both empty",
)

# Versions minimales de rclone. bisync existe depuis 1.58 ;
# --conflict-resolve et --resync-mode depuis 1.66. Raspberry Pi OS Bookworm
# livre 1.60 par apt : on doit donc savoir se passer des options récentes
# plutôt que d'échouer sur « unknown flag ».
RCLONE_MIN_BISYNC = (1, 58)
RCLONE_MIN_CONFLICT_RESOLVE = (1, 66)

TIMER_TEMPLATE = """[Unit]
Description=Execute rclone-sync.service toutes les {minutes} minute(s) (spec Sec.5.4)

[Timer]
OnBootSec={minutes}min
OnUnitActiveSec={minutes}min
AccuracySec=30s
Unit=rclone-sync.service

[Install]
WantedBy=timers.target
"""


def read_config() -> dict:
    try:
        data = json.loads(config.RCLONE_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def write_config(cfg: dict) -> None:
    """Écriture atomique (fichier temporaire + os.replace, cohérent avec status_io.py)."""
    tmp_path = config.RCLONE_CONFIG_FILE.with_suffix(config.RCLONE_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, config.RCLONE_CONFIG_FILE)


def ensure_config_exists() -> None:
    if not config.RCLONE_CONFIG_FILE.exists():
        write_config(dict(DEFAULT_CONFIG))


def _log(level: str, message: str) -> None:
    """Écrit dans logs/rclone.log dans un format simple, propre à relire (§5.4)."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"{timestamp} {level} {message}\n"
    try:
        with config.RCLONE_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        logger.exception("Impossible d'écrire dans %s", config.RCLONE_LOG)
    getattr(logger, level.lower(), logger.info)(message)


def _tail_log_lines(n: int = LOG_TAIL_LINES) -> List[str]:
    try:
        with config.RCLONE_LOG.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-n:]]


def run_copy_messages() -> dict:
    """Exécute `rclone copy` (jamais `sync`) ; échec toléré (retry au cycle suivant, §5.4)."""
    cfg = read_config()
    if not cfg.get("actif", True):
        _log("INFO", "Synchronisation désactivée (actif=false), cycle ignoré.")
        return {"ok": True, "skipped": True, "message": "Synchronisation désactivée."}

    remote, dossier = cfg.get("remote", config.RCLONE_REMOTE), cfg.get("dossier", config.RCLONE_FOLDER)

    if not shutil.which("rclone"):
        _log("ERROR", "rclone introuvable sur ce système.")
        return {"ok": False, "message": "rclone introuvable sur ce système."}

    config.MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    dest = f"{remote}:{dossier}"

    try:
        result = subprocess.run(
            ["rclone", "copy", str(config.MESSAGES_DIR), dest],
            capture_output=True, text=True, timeout=config.RCLONE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        _log("ERROR", f"Délai dépassé lors de la synchronisation vers {dest}.")
        return {"ok": False, "message": "Délai dépassé lors de la synchronisation."}
    except OSError as exc:
        _log("ERROR", f"Impossible de lancer rclone : {exc}")
        return {"ok": False, "message": f"Impossible de lancer rclone : {exc}"}

    if result.returncode != 0:
        detail_lines = (result.stderr or result.stdout or "échec inconnu").strip().splitlines()
        detail = detail_lines[-1] if detail_lines else "échec inconnu"
        # Échec toléré (§5.4) : pas d'internet au moment de l'exécution -> on
        # journalise et on laisse le prochain cycle du timer retenter.
        _log("WARNING", f"Échec de synchronisation vers {dest} : {detail}")
        return {"ok": False, "message": detail}

    _log("INFO", f"Synchronisation réussie vers {dest}.")
    return {"ok": True, "message": "Synchronisation réussie."}


def count_pending_files(remote: str, dossier: str) -> Optional[int]:
    """Nombre de fichiers qu'un `rclone copy` transfèrerait, sans rien modifier (--dry-run)."""
    if not shutil.which("rclone"):
        return None
    try:
        result = subprocess.run(
            ["rclone", "copy", str(config.MESSAGES_DIR), f"{remote}:{dossier}", "--dry-run", "-v"],
            capture_output=True, text=True, timeout=config.RCLONE_DRYRUN_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    output = result.stdout + result.stderr
    return len(re.findall(r": Copied \(new\)|: Copied \(replaced existing\)", output))


# --- Jambe bidirectionnelle : audio_src/ <-> Drive (bisync) -------------


def rclone_version() -> Optional[Tuple[int, int, int]]:
    """Version de rclone installée, ou None si illisible."""
    if not shutil.which("rclone"):
        return None
    try:
        sortie = subprocess.run(["rclone", "version"], capture_output=True,
                                text=True, timeout=10).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    trouve = re.search(r"rclone\s+v(\d+)\.(\d+)(?:\.(\d+))?", sortie)
    if not trouve:
        return None
    majeur, mineur, correctif = trouve.groups()
    return (int(majeur), int(mineur), int(correctif or 0))


def _resync_mode_supporte() -> bool:
    """rclone sait-il faire un --resync « le plus récent gagne » ?

    Sans --resync-mode (< 1.66), le resync prend path1 comme référence et
    supprime côté Drive tout ce qui n'existe pas localement. Une version
    illisible est traitée comme récente : mieux vaut laisser rclone refuser un
    drapeau inconnu que bloquer une installation à jour.
    """
    version = rclone_version()
    return version is None or version >= RCLONE_MIN_CONFLICT_RESOLVE


@contextlib.contextmanager
def _bisync_lock():
    """Verrou exclusif inter-processus, non bloquant.

    Le timer systemd et le bouton « Synchroniser maintenant » du dashboard ne
    doivent jamais lancer deux bisync en même temps — rclone corrompt son état
    interne si on le fait. Le cycle est simplement sauté si le verrou est déjà
    tenu. Le chemin est résolu à l'appel, pas à l'import : les tests
    redirigent config.LOGS_DIR vers un dossier temporaire.
    """
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    verrou = None
    try:
        verrou = (config.LOGS_DIR / LOCK_FILE_NAME).open("w")
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if verrou is not None:
            verrou.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(verrou, fcntl.LOCK_UN)
        verrou.close()


def _bisync_command(cfg: dict, resync: bool) -> Tuple[List[str], List[str]]:
    """Construit la commande bisync ; retourne (commande, avertissements)."""
    dest = f"{cfg.get('remote', config.RCLONE_REMOTE)}:{cfg.get('sources_dossier', config.RCLONE_SOURCES_FOLDER)}"
    cmd = ["rclone", "bisync", str(config.AUDIO_SRC_DIR), dest,
           "--max-delete", str(config.RCLONE_BISYNC_MAX_DELETE_PCT),
           "--create-empty-src-dirs=false"]
    avertissements = []

    version = rclone_version()
    if version is not None and version >= RCLONE_MIN_CONFLICT_RESOLVE:
        # En cas d'édition des deux côtés, le plus récent gagne et l'autre est
        # conservé sous un nom suffixé : rien n'est jamais perdu.
        cmd += ["--conflict-resolve", "newer", "--conflict-loser", "pathname",
                "--resilient", "--recover"]
        if resync:
            # Sans --resync-mode, le premier resync prend path1 (le Pi) comme
            # référence et supprimerait du Drive tout ce qui n'est pas encore
            # descendu. « newer » garde le plus récent des deux côtés.
            cmd += ["--resync-mode", "newer"]
    elif version is not None:
        avertissements.append(
            f"rclone v{version[0]}.{version[1]} : --conflict-resolve indisponible "
            f"(requiert {RCLONE_MIN_CONFLICT_RESOLVE[0]}.{RCLONE_MIN_CONFLICT_RESOLVE[1]}) ; "
            "en cas d'édition simultanée des deux côtés, rclone signalera un conflit "
            "sans le résoudre.")

    if resync:
        cmd.append("--resync")
    return cmd, avertissements


def run_bisync_sources(resync: bool = False) -> dict:
    """Synchronise audio_src/ dans les deux sens avec le Drive (§5.4).

    Le premier passage a besoin d'un `--resync` pour établir l'état de
    référence ; il est déclenché automatiquement tant que le témoin
    `sources_resync_fait` est faux. Si rclone signale ensuite que son état est
    perdu, le témoin est remis à zéro et un `--resync` est retenté une fois.
    """
    cfg = read_config()
    if not cfg.get("sources_actif", False) and not resync:
        return {"ok": True, "skipped": True,
                "message": "Synchronisation des sources audio désactivée."}

    if not shutil.which("rclone"):
        _log("ERROR", "rclone introuvable sur ce système.")
        return {"ok": False, "message": "rclone introuvable sur ce système."}

    version = rclone_version()
    if version is not None and version < RCLONE_MIN_BISYNC:
        message = (f"rclone v{version[0]}.{version[1]} trop ancien pour bisync "
                   f"(requiert {RCLONE_MIN_BISYNC[0]}.{RCLONE_MIN_BISYNC[1]}). "
                   "Installez une version récente via https://rclone.org/install.sh.")
        _log("ERROR", message)
        return {"ok": False, "message": message}

    config.AUDIO_SRC_DIR.mkdir(parents=True, exist_ok=True)

    with _bisync_lock() as obtenu:
        if not obtenu:
            _log("INFO", "Bisync déjà en cours ailleurs, cycle ignoré.")
            return {"ok": True, "skipped": True,
                    "message": "Une synchronisation bidirectionnelle est déjà en cours."}

        besoin_resync = resync or not cfg.get("sources_resync_fait", False)

        # Sans --resync-mode (rclone < 1.66), le tout premier --resync prend
        # le Pi comme référence et SUPPRIME du Drive tout ce qui n'y est pas
        # encore descendu. On ne le déclenche alors jamais tout seul : il faut
        # le bouton du dashboard ou `--resync` en ligne de commande, en
        # connaissance de cause.
        if besoin_resync and not resync and not _resync_mode_supporte():
            message = ("Synchronisation bidirectionnelle jamais initialisée et "
                       "rclone trop ancien pour un --resync sans risque : lancez-la "
                       "explicitement depuis le dashboard (« Réinitialiser la "
                       "synchronisation bidirectionnelle ») après avoir vérifié le "
                       "contenu du dossier Drive.")
            _log("WARNING", message)
            return {"ok": False, "message": message}

        resultat = _lancer_bisync(cfg, besoin_resync)

        # État perdu côté rclone : on retente une fois avec --resync, sauf si
        # c'est précisément ce qu'on vient de faire.
        if not resultat["ok"] and not besoin_resync and resultat.get("resync_requis"):
            _log("WARNING", "État bisync perdu : nouvelle tentative avec --resync.")
            resultat = _lancer_bisync(cfg, True)

        cfg_courant = read_config()
        if resultat["ok"] and not cfg_courant.get("sources_resync_fait", False):
            cfg_courant["sources_resync_fait"] = True
            write_config(cfg_courant)
        elif not resultat["ok"] and resultat.get("resync_requis"):
            cfg_courant["sources_resync_fait"] = False
            write_config(cfg_courant)

        return resultat


def _lancer_bisync(cfg: dict, resync: bool) -> dict:
    """Un passage de bisync. Le verrou est supposé déjà tenu par l'appelant."""
    cmd, avertissements = _bisync_command(cfg, resync)
    for avertissement in avertissements:
        _log("WARNING", avertissement)
    if resync:
        _log("INFO", "Initialisation de la synchronisation bidirectionnelle "
                     "(--resync) : ce premier passage peut être long.")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=config.RCLONE_BISYNC_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        _log("ERROR", "Délai dépassé pendant la synchronisation bidirectionnelle.")
        return {"ok": False, "message": "Délai dépassé pendant la synchronisation "
                                        "bidirectionnelle des sources audio."}
    except OSError as exc:
        _log("ERROR", f"Impossible de lancer rclone bisync : {exc}")
        return {"ok": False, "message": f"Impossible de lancer rclone bisync : {exc}"}

    sortie = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0:
        lignes = sortie.strip().splitlines()
        detail = lignes[-1] if lignes else "échec inconnu"
        resync_requis = any(motif in sortie.lower() for motif in BISYNC_RESYNC_REQUIS)
        # Échec toléré comme pour la copie montante : pas d'internet au moment
        # du cycle, on journalise et on laisse le timer retenter.
        _log("WARNING", f"Échec de la synchronisation bidirectionnelle : {detail}")
        return {"ok": False, "message": detail, "resync_requis": resync_requis}

    _log("INFO", "Bisync audio_src réussi.")
    return {"ok": True, "message": "Sources audio synchronisées dans les deux sens.",
            "resync": resync}


def run_sync() -> dict:
    """Un cycle complet : messages/ en montant, puis audio_src/ en bidirectionnel.

    Les deux jambes sont exécutées en séquence, et `messages/` d'abord : les
    enregistrements des invités sont la charge précieuse, ils ne doivent pas
    attendre qu'un problème de bisync soit réglé.
    """
    messages = run_copy_messages()
    sources = run_bisync_sources()

    ok = messages.get("ok", False) and sources.get("ok", False)
    morceaux = [messages.get("message", "")]
    if not sources.get("skipped"):
        morceaux.append(sources.get("message", ""))

    return {"ok": ok, "messages": messages, "sources": sources,
            "message": " ".join(m for m in morceaux if m)}


def sources_status() -> dict:
    """Statut de la jambe bidirectionnelle, pour la page /rclone du dashboard."""
    cfg = read_config()
    derniere = None
    for line in _tail_log_lines():
        if " INFO Bisync audio_src réussi" in line:
            derniere = line[:19]

    version = rclone_version()
    return {
        "actif": cfg.get("sources_actif", False),
        "dossier": cfg.get("sources_dossier", config.RCLONE_SOURCES_FOLDER),
        "resync_fait": cfg.get("sources_resync_fait", False),
        "derniere_sync_reussie": derniere,
        "fichiers_locaux": len(list(config.AUDIO_SRC_DIR.glob("*")))
                            if config.AUDIO_SRC_DIR.exists() else 0,
        "rclone_version": ".".join(str(n) for n in version) if version else None,
        "bisync_supporte": version is None or version >= RCLONE_MIN_BISYNC,
    }


def get_status() -> dict:
    """Statut pour le dashboard (§5.2) : dernière sync réussie, fichiers en attente, erreurs récentes."""
    cfg = read_config()
    last_success = None
    recent_errors: List[str] = []
    for line in _tail_log_lines():
        if " INFO Synchronisation réussie" in line:
            last_success = line[:19]
        elif " ERROR " in line or " WARNING " in line:
            recent_errors.append(line)

    pending = None
    if cfg.get("actif", True):
        pending = count_pending_files(cfg.get("remote", config.RCLONE_REMOTE), cfg.get("dossier", config.RCLONE_FOLDER))

    return {
        "remote": cfg.get("remote", config.RCLONE_REMOTE),
        "dossier": cfg.get("dossier", config.RCLONE_FOLDER),
        "intervalle_min": cfg.get("intervalle_min", config.RCLONE_INTERVAL_MIN),
        "actif": cfg.get("actif", True),
        "derniere_sync_reussie": last_success,
        "fichiers_en_attente": pending,
        "erreurs_recentes": recent_errors[-RECENT_ERRORS_COUNT:],
        # Clés plates conservées telles quelles (le gabarit et rclone.js les
        # lisent directement) ; la jambe bidirectionnelle s'ajoute à côté.
        "sources": sources_status(),
    }


def regenerate_timer_unit(minutes: int) -> None:
    """Réécrit systemd/rclone-sync.timer (symlinké depuis /etc/systemd/system, §5.4)."""
    config.SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    content = TIMER_TEMPLATE.format(minutes=minutes)
    tmp_path = config.SYSTEMD_RCLONE_TIMER_FILE.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, config.SYSTEMD_RCLONE_TIMER_FILE)


def reload_and_restart_timer() -> Optional[subprocess.CompletedProcess]:
    """`systemctl daemon-reload` puis `restart` de l'unité, via la règle sudoers ciblée (§5.4).

    Retourne None si sudo/systemctl est indisponible ou a expiré (loggué),
    jamais d'exception : un échec ici ne doit pas empêcher d'avoir sauvegardé
    la configuration.
    """
    try:
        subprocess.run(["sudo", "systemctl", "daemon-reload"],
                        timeout=10, capture_output=True, text=True, check=False)
        return subprocess.run(["sudo", "systemctl", "restart", config.RCLONE_SYSTEMD_UNIT],
                               timeout=10, capture_output=True, text=True, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log("ERROR", f"Échec systemctl lors de la mise à jour de l'intervalle : {exc}")
        return None


def _verifier_dossiers_disjoints(dossier: str, sources_dossier: str) -> Optional[str]:
    """Message d'erreur si les deux dossiers Drive se recouvrent, None sinon."""
    a = (dossier or "").strip("/")
    b = (sources_dossier or "").strip("/")
    if not b:
        return "Le dossier Drive des sources audio ne peut pas être vide."
    if a == b or b.startswith(a + "/") or a.startswith(b + "/"):
        return ("Le dossier des sources audio doit être distinct de celui des "
                "enregistrements (et non imbriqué) : sinon les messages des "
                "invités deviendraient eux aussi synchronisés dans les deux sens, "
                "et une suppression côté Drive les effacerait.")
    return None


def update_config(remote: str, dossier: str, intervalle_min: int, actif: bool,
                  sources_dossier: Optional[str] = None,
                  sources_actif: Optional[bool] = None) -> dict:
    """Met à jour rclone_config.json ; régénère et recharge le timer si l'intervalle a changé (§5.4)."""
    previous = read_config()
    if sources_dossier is None:
        sources_dossier = previous.get("sources_dossier", config.RCLONE_SOURCES_FOLDER)
    if sources_actif is None:
        sources_actif = previous.get("sources_actif", False)

    # Les deux jambes n'ont pas les mêmes règles : messages/ est montant seul
    # pour qu'aucune action côté Drive ne puisse effacer un enregistrement
    # d'invité. Partager le dossier — ou l'imbriquer — rendrait de fait les
    # enregistrements bidirectionnels.
    erreur_dossiers = _verifier_dossiers_disjoints(dossier, sources_dossier)
    if erreur_dossiers:
        return {"ok": False, "timer_regenerated": False, "erreur": erreur_dossiers}

    # sources_resync_fait est un état interne, jamais saisi dans le
    # formulaire : on le conserve, sauf si le dossier Drive change — l'état de
    # référence de rclone ne vaut alors plus rien.
    resync_fait = previous.get("sources_resync_fait", False)
    if sources_dossier != previous.get("sources_dossier"):
        resync_fait = False

    write_config({"remote": remote, "dossier": dossier,
                  "intervalle_min": intervalle_min, "actif": actif,
                  "sources_dossier": sources_dossier, "sources_actif": sources_actif,
                  "sources_resync_fait": resync_fait})

    result = {"ok": True, "timer_regenerated": False, "erreur": None}
    if previous.get("intervalle_min") != intervalle_min:
        try:
            regenerate_timer_unit(intervalle_min)
        except OSError as exc:
            _log("ERROR", f"Impossible de régénérer le timer : {exc}")
            result["ok"] = False
            result["erreur"] = f"Impossible de régénérer le timer : {exc}"
            return result

        result["timer_regenerated"] = True
        reload_result = reload_and_restart_timer()
        if reload_result is None:
            result["ok"] = False
            result["erreur"] = "systemctl indisponible (sudo non configuré ?) : intervalle enregistré mais pas encore actif."
        elif reload_result.returncode != 0:
            result["ok"] = False
            result["erreur"] = f"systemctl a échoué : {reload_result.stderr.strip()}"
        else:
            _log("INFO", f"Intervalle de synchronisation changé à {intervalle_min} min, timer rechargé.")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="Exécute un cycle complet immédiatement (messages + sources audio).")
    parser.add_argument("--resync", action="store_true",
                        help="Réinitialise la synchronisation bidirectionnelle de audio_src/ "
                             "(premier passage, ou après une perte d'état de rclone).")
    args = parser.parse_args()

    if args.resync:
        config.ensure_directories()
        ensure_config_exists()
        result = run_bisync_sources(resync=True)
        print(result.get("message", ""))
        raise SystemExit(0 if result.get("ok") else 1)

    if args.run:
        config.ensure_directories()
        ensure_config_exists()
        result = run_sync()
        print(result.get("message", ""))
        raise SystemExit(0 if result.get("ok") else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
