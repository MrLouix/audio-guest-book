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
import logging.handlers
import subprocess
from typing import Optional

import config
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


def _service_is_failed(name: str) -> bool:
    try:
        result = subprocess.run(["systemctl", "is-failed", "--quiet", name], timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _restart_service(name: str, reason: str) -> None:
    logger.warning("Watchdog : redémarrage de %s (%s).", name, reason)
    subprocess.run(["systemctl", "reset-failed", name], timeout=10, check=False)
    subprocess.run(["systemctl", "restart", name], timeout=10, check=False)


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
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(
        config.LIVRE_DOR_LOG, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def main() -> None:
    config.ensure_directories()
    _setup_logging()
    check_and_restart_if_needed()


if __name__ == "__main__":
    main()
