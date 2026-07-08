#!/usr/bin/env bash
# Installation unique (root) des services systemd principaux (§7.1) :
# livre-dor, dashboard, watchdog applicatif, bascule wifi/AP. La
# synchronisation rclone a son propre script d'installation
# (scripts/setup_rclone_systemd.sh), car elle nécessite en plus une règle
# sudoers dédiée.
#
# Comme pour rclone (§5.4), les unités sont symlinkées depuis le dépôt.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script doit être exécuté avec sudo/root." >&2
    exit 1
fi

UNITS=(
    livre-dor.service
    dashboard.service
    livre-dor-watchdog.service
    livre-dor-watchdog.timer
    wifi-or-ap.service
    wifi-or-ap.timer
)

echo "== Symlink des unités systemd =="
for unit in "${UNITS[@]}"; do
    ln -sf "$PROJECT_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done

echo "== Activation =="
systemctl daemon-reload
systemctl enable --now livre-dor.service dashboard.service \
    livre-dor-watchdog.timer wifi-or-ap.timer

echo "Services installés et démarrés."
echo "Vérifier :  systemctl status livre-dor.service dashboard.service"
echo "Logs      :  journalctl -u livre-dor.service -f   (idem -u dashboard.service, -u wifi-or-ap.service)"
echo
echo "N'oubliez pas scripts/setup_rclone_systemd.sh pour activer la synchronisation Google Drive."
