#!/usr/bin/env bash
# Mise en place de l'environnement système sur le Raspberry Pi (Sprint 0).
# À exécuter une fois sur le Pi, avec les droits sudo.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Paquets système =="
sudo apt-get update
sudo apt-get install -y alsa-utils rclone python3-pip python3-venv python3-full ffmpeg avahi-daemon

echo "== Hostname mDNS (livredor.local) =="
sudo hostnamectl set-hostname livredor
sudo systemctl restart avahi-daemon

echo "== Environnement virtuel Python =="
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "== Arborescence du projet =="
python3 - "$PROJECT_DIR/src" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import config
config.ensure_directories()
print("Dossiers vérifiés :", config.AUDIO_SRC_DIR, config.AUDIO_DIR,
      config.MESSAGES_DIR, config.LOGS_DIR, config.STATIC_DIR)
PY

echo "== Version de rclone =="
# bisync existe depuis 1.58, --conflict-resolve et --resync-mode depuis 1.66.
# Bookworm livre 1.60 par apt : sans --resync-mode, le premier --resync prend
# le Pi comme référence et supprimerait du Drive ce qui n'y est pas encore
# descendu, donc la synchro bidirectionnelle refusera de s'initialiser seule.
RCLONE_VERSION="$(rclone version 2>/dev/null | head -1 | sed -n 's/^rclone v\([0-9.]*\).*/\1/p')"
if [ -z "$RCLONE_VERSION" ]; then
    echo "ATTENTION : impossible de lire la version de rclone."
else
    RCLONE_MAJEUR="${RCLONE_VERSION%%.*}"
    RCLONE_MINEUR="$(echo "$RCLONE_VERSION" | cut -d. -f2)"
    echo "rclone v$RCLONE_VERSION détecté."
    if [ "$RCLONE_MAJEUR" -lt 1 ] || { [ "$RCLONE_MAJEUR" -eq 1 ] && [ "$RCLONE_MINEUR" -lt 66 ]; }; then
        echo "ATTENTION : rclone v$RCLONE_VERSION est trop ancien pour la synchronisation"
        echo "bidirectionnelle de audio_src/ (1.66 minimum recommandé). Pour le mettre à jour :"
        echo "    curl https://rclone.org/install.sh | sudo bash"
    fi
fi

echo "== Carte son (IQaudio Codec Zero) =="
if aplay -l 2>/dev/null | grep -qi 'IQaudIO\|Codec'; then
    echo "Codec Zero détecté :"
    aplay -l | grep -i 'IQaudIO\|Codec'
    echo "Vérifiez que config.SOUND_CARD (ou la variable d'environnement SOUND_CARD) correspond bien à cette carte."

    echo "== Configuration du codec et sauvegarde de l'état ALSA =="
    # Une seule fois, sous root : le service tourne sous un utilisateur non
    # privilégié et ne peut pas écrire /var/lib/alsa/asound.state. Figer
    # l'état ici rend la carte correcte dès le boot (alsactl restore), avant
    # même que le service démarre.
    if sudo "$PROJECT_DIR/scripts/audio-setup.sh" headphone; then
        echo "Codec configuré et état ALSA sauvegardé."
    else
        echo "ATTENTION : la configuration du codec a échoué. Diagnostic :"
        echo "    $PROJECT_DIR/scripts/audio-setup.sh status"
    fi
else
    echo "ATTENTION : aucune carte IQaudio Codec Zero détectée par 'aplay -l'."
    echo "Vérifiez le montage du HAT, puis relancez ce script."
fi

echo "Installation terminée."
echo
echo "Étapes suivantes :"
echo "  1. Déposer les fichiers sources (sonnerie, messages) dans $PROJECT_DIR/audio_src/"
echo "  2. python3 $PROJECT_DIR/src/prepare_audio.py   # conversion 48 kHz stéréo"
echo "  3. rclone config                               # remote Google Drive"
echo "  4. Depuis le dashboard : /settings pour choisir chaque son,"
echo "     /rclone pour activer et initialiser la synchronisation bidirectionnelle."
