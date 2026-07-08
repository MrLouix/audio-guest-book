#!/usr/bin/env bash
# Installation unique (root) du service/timer rclone-sync + règle sudoers
# ciblée (§5.4). À exécuter une fois sur le Pi après `rclone config`.
#
# Les unités systemd sont symlinkées depuis le dépôt plutôt que copiées :
# le dashboard (utilisateur non privilégié, ex. `pi`) peut ainsi régénérer
# le contenu de systemd/rclone-sync.timer en le réécrivant directement dans
# le dépôt, sans avoir besoin d'écrire dans /etc/systemd/system. Seuls
# `systemctl daemon-reload` et `systemctl restart/start rclone-sync.timer`
# nécessitent une élévation de privilège, accordée ici via une règle
# sudoers strictement limitée à ces trois commandes (jamais un NOPASSWD
# global — c'est le point de sécurité identifié par la spec).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${LIVRE_DOR_USER:-pi}"
SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script doit être exécuté avec sudo/root." >&2
    exit 1
fi

echo "== Symlink des unités systemd =="
ln -sf "$PROJECT_DIR/systemd/rclone-sync.service" /etc/systemd/system/rclone-sync.service
ln -sf "$PROJECT_DIR/systemd/rclone-sync.timer" /etc/systemd/system/rclone-sync.timer

echo "== Règle sudoers ciblée (${RUN_USER}) =="
SUDOERS_FILE=/etc/sudoers.d/livredor-rclone
cat > "$SUDOERS_FILE" <<EOF
# Généré par scripts/setup_rclone_systemd.sh — NE PAS élargir cette règle
# (voir §5.4 : jamais de NOPASSWD global).
${RUN_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} daemon-reload, ${SYSTEMCTL_BIN} restart rclone-sync.timer, ${SYSTEMCTL_BIN} start rclone-sync.timer
EOF
chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE"

echo "== Activation du timer =="
systemctl daemon-reload
systemctl enable --now rclone-sync.timer

echo "Service/timer rclone-sync installés ; sudoers ciblé configuré pour '${RUN_USER}'."
