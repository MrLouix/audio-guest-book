#!/usr/bin/env bash
# Mise à jour à distance du livre d'or, à lancer par ssh dès que le Pi est
# joignable :
#   1. enregistre un réseau WiFi (facultatif) ;
#   2. met à jour le code (git pull) ;
#   3. réinstalle les dépendances Python si requirements.txt a changé ;
#   4. relance dashboard et livre-dor, une fois le téléphone raccroché.
#
# Le réseau est seulement ENREGISTRÉ, sous un profil NetworkManager nommé
# d'après son SSID, comme ceux que scripts/wifi_or_ap.sh reconnaît : le Pi
# ne s'y connecte pas sur-le-champ, la session ssh en cours n'est donc pas
# coupée. wifi_or_ap.sh le choisira de lui-même quand il sera à portée.
#
# Usage, sur le Pi, depuis le dépôt :
#   ./scripts/mise_a_jour.sh
#   ./scripts/mise_a_jour.sh --wifi "SSID" --mdp "mot de passe"
#   ./scripts/mise_a_jour.sh --wifi "SSID"     # mot de passe demandé (ssh -t)
#
# Sans copie préalable sur le Pi, le script voyage par ssh :
#   Windows : scripts\mise_a_jour_distance.ps1 (attend aussi que le Pi réponde)
#   Linux / macOS :
#     ssh pi@livredor.local 'bash -s -- --wifi "SSID" --mdp "..."' < scripts/mise_a_jour.sh
#
# Options :
#   --branche NOM      passe sur cette branche avant la mise à jour
#   --depot CHEMIN     dépôt sur le Pi (défaut : celui du script, sinon le premier
#                      de ~/livre_dor, ~/audio-guest-book, ~/audio_guest_book)
#   --attente-max SEC  attente du raccroché avant d'abandonner la relance (défaut 600)
#   --forcer           relance même en pleine communication
set -euo pipefail

WIFI_SSID=""
WIFI_MDP=""
BRANCHE=""
DEPOT=""
ATTENTE_MAX=600
FORCER=""
SERVICES=(dashboard livre-dor)
# États où le téléphone est au repos (status_io.ETATS_AU_REPOS) : relancer
# livre-dor ailleurs couperait un invité en plein message.
ETATS_AU_REPOS=" attente erreur "

erreur() { echo "ERREUR : $*" >&2; exit 1; }
etape() { printf '\n== %s ==\n' "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --wifi) WIFI_SSID="${2:?--wifi attend un SSID}"; shift 2 ;;
        --mdp) WIFI_MDP="${2:?--mdp attend un mot de passe}"; shift 2 ;;
        --branche) BRANCHE="${2:?--branche attend un nom}"; shift 2 ;;
        --depot) DEPOT="${2:?--depot attend un chemin}"; shift 2 ;;
        --attente-max) ATTENTE_MAX="${2:?--attente-max attend des secondes}"; shift 2 ;;
        --forcer) FORCER=1; shift ;;
        *) erreur "option inconnue : $1 (voir l'en-tête du script)" ;;
    esac
done

[ "$(id -u)" -ne 0 ] || erreur "à lancer sans sudo : git et le venv appartiennent à l'utilisateur du dépôt."

# Dépôt : celui du script s'il est lancé depuis le dépôt ; lu sur stdin
# (bash -s, via ssh), il n'a pas de chemin : on cherche ~/livre_dor (§8),
# puis les noms d'un simple git clone.
if [ -z "$DEPOT" ]; then
    source_script="${BASH_SOURCE[0]:-}"
    if [ -n "$source_script" ] && [ -f "$source_script" ] \
            && [ -d "$(dirname "$source_script")/../.git" ]; then
        DEPOT="$(cd "$(dirname "$source_script")/.." && pwd)"
    else
        for candidat in "$HOME/livre_dor" "$HOME/audio-guest-book" "$HOME/audio_guest_book"; do
            if [ -d "$candidat/.git" ]; then DEPOT="$candidat"; break; fi
        done
        DEPOT="${DEPOT:-$HOME/livre_dor}"
    fi
fi
[ -d "$DEPOT/.git" ] || erreur "dépôt introuvable : $DEPOT (précisez --depot)"
cd "$DEPOT"
echo "Dépôt : $DEPOT"

# --- 1. Réseau WiFi -------------------------------------------------------

if [ -n "$WIFI_SSID" ]; then
    etape "Réseau WiFi « $WIFI_SSID »"
    if [ -z "$WIFI_MDP" ]; then
        [ -t 0 ] || erreur "mot de passe WiFi manquant : --mdp, ou ssh -t pour le saisir"
        read -rsp "Mot de passe WiFi : " WIFI_MDP
        echo
    fi
    [ "${#WIFI_MDP}" -ge 8 ] || erreur "un mot de passe WPA fait au moins 8 caractères"
    if nmcli -t -f NAME connection show | grep -Fxq "$WIFI_SSID"; then
        sudo nmcli connection modify "$WIFI_SSID" 802-11-wireless.ssid "$WIFI_SSID" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_MDP" connection.autoconnect yes
        echo "Profil existant mis à jour."
    else
        sudo nmcli connection add type wifi ifname "*" con-name "$WIFI_SSID" ssid "$WIFI_SSID" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_MDP" connection.autoconnect yes >/dev/null
        echo "Profil créé : le Pi s'y connectera quand ce réseau sera à portée."
    fi
fi

# --- 2. Code --------------------------------------------------------------

etape "Code"
avant="$(git rev-parse HEAD)"
# Modifications locales (le dashboard réécrit systemd/rclone-sync.timer quand
# l'intervalle de synchro change) : mises de côté le temps de la mise à jour.
mis_de_cote=""
if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -q -m "mise_a_jour.sh $(date '+%F %T')"
    mis_de_cote=1
    echo "Modifications locales mises de côté (git stash)."
fi
remettre() {
    if [ -n "$mis_de_cote" ] && ! git stash pop -q; then
        echo "ATTENTION : modifications locales non réappliquées, voir « git stash list »." >&2
    fi
}
if [ -n "$BRANCHE" ]; then
    git fetch -q origin "$BRANCHE" || { remettre; erreur "branche $BRANCHE introuvable sur origin"; }
    git checkout -q "$BRANCHE" || { remettre; erreur "impossible de passer sur $BRANCHE"; }
fi
git pull -q --ff-only || { remettre; erreur "git pull impossible (réseau, ou historique divergent)"; }
remettre
apres="$(git rev-parse HEAD)"
echo "Branche : $(git branch --show-current)"
if [ "$avant" = "$apres" ]; then
    echo "Déjà à jour ($(git log -1 --format='%h %s'))."
else
    git log --oneline "$avant..$apres"
fi

# --- 3. Dépendances et unités systemd -------------------------------------

change() { [ "$avant" != "$apres" ] && [ -n "$(git diff --name-only "$avant" "$apres" -- "$@")" ]; }

if change requirements.txt; then
    etape "Dépendances Python"
    [ -x .venv/bin/pip ] || erreur "venv .venv/ absent : lancer ./scripts/install.sh"
    .venv/bin/pip install -q -r requirements.txt
    echo "requirements.txt réinstallé dans .venv/."
fi
if change systemd/; then
    etape "Unités systemd"
    sudo systemctl daemon-reload
    echo "Unités modifiées : systemd rechargé."
fi

# --- 4. Relance des services ----------------------------------------------

etat_telephone() {
    python3 - "$DEPOT/status.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("etat") or "")
except (OSError, ValueError, AttributeError):
    print("")
PY
}

etape "Relance de ${SERVICES[*]}"
if [ -z "$FORCER" ]; then
    debut=$SECONDS
    while :; do
        etat="$(etat_telephone)"
        # Pas de status.json, ou état au repos : on peut relancer.
        if [ -z "$etat" ] || [[ "$ETATS_AU_REPOS" == *" $etat "* ]]; then
            break
        fi
        if (( SECONDS - debut >= ATTENTE_MAX )); then
            erreur "téléphone toujours « $etat » après ${ATTENTE_MAX} s : services non relancés (relancer avec --forcer)."
        fi
        echo "Téléphone en cours d'utilisation (« $etat ») : attente du raccroché…"
        sleep 10
    done
fi
sudo systemctl restart "${SERVICES[@]}"
sleep 3
echec=""
for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
        echo "$service : actif"
    else
        echo "$service : NON ACTIF" >&2
        journalctl -u "$service" -n 20 --no-pager >&2 || true
        echec=1
    fi
done
[ -z "$echec" ] || erreur "un service n'a pas redémarré (journal ci-dessus)."

echo
echo "Mise à jour terminée : $(git log -1 --format='%h %s')"
