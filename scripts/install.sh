#!/usr/bin/env bash
# Mise en place de l'environnement système sur le Raspberry Pi (Sprint 0).
# À exécuter une fois sur le Pi, avec les droits sudo.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Paquets système =="
sudo apt-get update
sudo apt-get install -y alsa-utils rclone python3-pip python3-venv ffmpeg avahi-daemon

echo "== Hostname mDNS (livredor.local) =="
sudo hostnamectl set-hostname livredor
sudo systemctl restart avahi-daemon

echo "== Environnement virtuel Python =="
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "== Arborescence du projet =="
python3 - "$PROJECT_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import config
config.ensure_directories()
print("Dossiers vérifiés :", config.AUDIO_SRC_DIR, config.AUDIO_DIR,
      config.MESSAGES_DIR, config.LOGS_DIR, config.STATIC_DIR)
PY

echo "== Carte son USB =="
if aplay -l 2>/dev/null | grep -qi usb; then
    echo "Carte son USB détectée :"
    aplay -l | grep -i usb
    echo "Vérifiez que config.SOUND_CARD (ou la variable d'environnement SOUND_CARD) correspond bien à cette carte."
else
    echo "ATTENTION : aucune carte son USB détectée par 'aplay -l'. Branchez-la puis relancez ce script."
fi

echo "Installation terminée."
