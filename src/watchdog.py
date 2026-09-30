"""Watchdog applicatif : ne jamais laisser un service abandonné définitivement (§7.1).

Exécuté périodiquement par livre-dor-watchdog.timer. Deux mécanismes :

1. Service systemd en état "failed" (son StartLimitBurst a été épuisé après
   une rafale de crashs rapprochés) : on ne renonce jamais - `systemctl
   reset-failed` puis `restart` sont retentés à chaque exécution de ce
   timer, dont l'intervalle est volontairement plus long que RestartSec
   pour laisser le temps à une panne transitoire de se résorber.
2. status.json périmé (spécifique à livre-dor.service, seul à l'écrire) :
   le processus peut tourner sans être "failed" aux yeux de systemd tout en
   étant gelé (bloqué, boucle infinie...) ; si sa dernière mise à jour date
   de plus de WATCHDOG_STALE_AFTER_SEC, on le redémarre.
"""

import datetime
import logging
import subprocess
from typing import Optional

import config
import fichiers
import status_io

logger = logging.getLogger(__name__)

# Tous les services longs surveillés pour le cas 1 (StartLimitBurst épuisé).
MANAGED_SERVICES = [
    "livre-dor.service",
    "dashboard.service",
    "wifi-or-ap.service",
    "rclone-sync.service",
]

STATUS_OWNER_SERVICE = "livre-dor.service"

SYSTEMCTL_TIMEOUT_SEC = 10


def _systemctl(*args: str) -> Optional[int]:
    """Code de retour de `systemctl args…` ; None s'il n'a pas pu s'exécuter.

    Ne lève jamais : un systemctl bloqué sur un service ne doit pas empêcher
    le watchdog de surveiller les suivants.
    """
    try:
        return subprocess.run(["systemctl", *args], timeout=SYSTEMCTL_TIMEOUT_SEC,
                              check=False).returncode
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("Watchdog : systemctl %s en échec : %s", " ".join(args), exc)
        return None


def _service_is_failed(name: str) -> bool:
    return _systemctl("is-failed", "--quiet", name) == 0


def _restart_service(name: str, reason: str) -> None:
    logger.warning("Watchdog : redémarrage de %s (%s).", name, reason)
    _systemctl("reset-failed", name)
    _systemctl("restart", name)


def _status_age_seconds() -> Optional[float]:
    data = status_io.read_status()
    if not data or not data.get("derniere_maj"):
        return None
    try:
        last = datetime.datetime.fromisoformat(data["derniere_maj"])
    except ValueError:
        return None
    return (datetime.datetime.now() - last).total_seconds()


def check_and_restart_if_needed() -> None:
    for name in MANAGED_SERVICES:
        if _service_is_failed(name):
            _restart_service(name, "service en échec (StartLimitBurst probablement épuisé)")

    age = _status_age_seconds()
    if age is None:
        return  # status.json absent : le service démarre peut-être encore.
    if age >= config.WATCHDOG_STALE_AFTER_SEC and not _service_is_failed(STATUS_OWNER_SERVICE):
        # Déjà redémarré ci-dessus s'il était "failed" ; ici il tourne
        # toujours mais ne progresse plus (gelé).
        _restart_service(STATUS_OWNER_SERVICE, f"status.json périmé depuis {age:.0f}s (seuil {config.WATCHDOG_STALE_AFTER_SEC}s)")


def _setup_logging() -> None:
    # Même fichier que livre_dor.py : ses décisions apparaissent dans les
    # journaux affichés par le dashboard.
    fichiers.configurer_journal(config.LIVRE_DOR_LOG, logging.INFO)


def main() -> None:
    config.ensure_directories()
    _setup_logging()
    check_and_restart_if_needed()


if __name__ == "__main__":
    main()
