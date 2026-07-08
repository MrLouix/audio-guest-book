"""Synchronisation Google Drive via rclone (§5.4).

`rclone copy` uniquement — jamais `sync` : ne fait qu'ajouter les fichiers
nouveaux ou modifiés, ne supprime jamais rien ni en local ni sur le Drive.
Configuration éditable depuis le dashboard (rclone_config.json, contrat du
§8), journalisée dans logs/rclone.log dans notre propre format (facile à
relire pour l'indicateur de statut du dashboard, plutôt que de dépendre du
format interne de rclone).

Peut aussi s'exécuter en CLI (`python3 src/rclone_sync.py --run`), ce que
fait le service systemd rclone-sync.service à chaque déclenchement du timer.
"""

import argparse
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from typing import List, Optional

import config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "remote": config.RCLONE_REMOTE,
    "dossier": config.RCLONE_FOLDER,
    "intervalle_min": config.RCLONE_INTERVAL_MIN,
    "actif": True,
}

LOG_TAIL_LINES = 200
RECENT_ERRORS_COUNT = 5

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


def run_sync() -> dict:
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


def update_config(remote: str, dossier: str, intervalle_min: int, actif: bool) -> dict:
    """Met à jour rclone_config.json ; régénère et recharge le timer si l'intervalle a changé (§5.4)."""
    previous = read_config()
    write_config({"remote": remote, "dossier": dossier, "intervalle_min": intervalle_min, "actif": actif})

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
    parser.add_argument("--run", action="store_true", help="Exécute un cycle de synchronisation immédiatement.")
    args = parser.parse_args()

    if args.run:
        config.ensure_directories()
        ensure_config_exists()
        result = run_sync()
        print(result.get("message", ""))
        raise SystemExit(0 if result.get("ok") else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
