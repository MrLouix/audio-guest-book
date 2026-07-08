#!/usr/bin/env bash
# Bascule WiFi/AP + surveillance de qualité de la connexion active (§5.3).
# Exécuté toutes les ~30s par wifi-or-ap.timer (systemd).
#
# Logique :
#   1. Déjà connecté à un vrai wifi (≠ profil AP) -> contrôle qualité (signal
#      + ping passerelle). Sain -> ne rien faire. En échec répété
#      (WIFI_FAIL_THRESHOLD fois de suite) -> blacklist temporaire du SSID
#      (WIFI_BLACKLIST_MIN minutes), déconnexion, et on retente aussitôt
#      l'étape 2 (pas d'attente du prochain cycle).
#   2. Sinon : profils connus dont le SSID est visible et non blacklisté,
#      triés par signal décroissant, tentés dans l'ordre (timeout
#      CONNECT_TIMEOUT_SEC chacun). Le point d'accès n'est coupé qu'au moment
#      où une vraie tentative de connexion commence (jamais avant, pour ne
#      pas laisser le Pi injoignable entre les deux).
#   3. Si aucun ne fonctionne -> repli sur le point d'accès local.
#
# Les profils de connexion nmcli sont supposés nommés d'après leur SSID (tel
# que créé par `nmcli device wifi connect` / /api/wifi/add, Sprint 7).
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LOG_FILE="${RESEAU_LOG:-$PROJECT_DIR/logs/reseau.log}"
STATE_DIR="${WIFI_STATE_DIR:-/run/livre_dor}"
HEALTH_FILE="${WIFI_HEALTH_FILE:-$STATE_DIR/wifi_health}"
BLACKLIST_FILE="${WIFI_BLACKLIST_FILE:-$STATE_DIR/wifi_blacklist}"

AP_CONNECTION_NAME="${AP_CONNECTION_NAME:-GuestbookAP}"
AP_SSID="${AP_SSID:-Livre-dor-Mariage}"
AP_PASSWORD="${AP_PASSWORD:-livredormariage}"
AP_IP="${AP_IP:-192.168.4.1}"
WIFI_SIGNAL_MIN="${WIFI_SIGNAL_MIN:-25}"
WIFI_FAIL_THRESHOLD="${WIFI_FAIL_THRESHOLD:-3}"
WIFI_BLACKLIST_MIN="${WIFI_BLACKLIST_MIN:-10}"
CONNECT_TIMEOUT_SEC="${CONNECT_TIMEOUT_SEC:-15}"

MAX_LOG_BYTES=1000000
MAX_LOG_BACKUPS=5

mkdir -p "$(dirname "$LOG_FILE")" "$STATE_DIR"

log() {
    local level="$1"; shift
    printf '%s %s %s\n' "$(date -Is)" "$level" "$*" >> "$LOG_FILE"
}

# --- Rotation basique du log (5 x 1 Mo, §7.3), reseau.log n'étant pas géré
#     par le logging Python (RotatingFileHandler) de livre_dor.py -----------
rotate_log_if_needed() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
    [ "$size" -ge "$MAX_LOG_BYTES" ] || return 0
    local i
    for (( i = MAX_LOG_BACKUPS - 1; i >= 1; i-- )); do
        [ -f "${LOG_FILE}.${i}" ] && mv "${LOG_FILE}.${i}" "${LOG_FILE}.$((i + 1))"
    done
    mv "$LOG_FILE" "${LOG_FILE}.1"
}

# --- État persisté : compteur d'échecs consécutifs + blacklist temporaire --

read_fail_count() {
    [ -f "$HEALTH_FILE" ] && cat "$HEALTH_FILE" 2>/dev/null || echo 0
}

write_fail_count() {
    local tmp
    tmp="$(mktemp "${HEALTH_FILE}.XXXXXX")"
    printf '%s' "$1" > "$tmp"
    mv "$tmp" "$HEALTH_FILE"
}

is_blacklisted() {
    local ssid="$1" now
    now="$(date +%s)"
    [ -f "$BLACKLIST_FILE" ] || return 1
    awk -F'\t' -v ssid="$ssid" -v now="$now" \
        '$1 == ssid && $2 > now { found=1 } END { exit !found }' "$BLACKLIST_FILE"
}

blacklist_ssid() {
    local ssid="$1" expiry tmp
    expiry=$(( $(date +%s) + WIFI_BLACKLIST_MIN * 60 ))
    tmp="$(mktemp "${BLACKLIST_FILE}.XXXXXX")"
    if [ -f "$BLACKLIST_FILE" ]; then
        awk -F'\t' -v ssid="$ssid" '$1 != ssid' "$BLACKLIST_FILE" > "$tmp"
    else
        : > "$tmp"
    fi
    printf '%s\t%s\n' "$ssid" "$expiry" >> "$tmp"
    mv "$tmp" "$BLACKLIST_FILE"
    log WARNING "SSID '$ssid' blacklisté temporairement pour ${WIFI_BLACKLIST_MIN} min (instabilité)."
}

# --- Lecture de l'état réseau courant --------------------------------------

get_active_wifi_connection_name() {
    nmcli -t -f TYPE,NAME connection show --active 2>/dev/null \
        | awk -F: '$1 == "802-11-wireless" { print $2; exit }'
}

get_gateway_ip() {
    ip route show default 2>/dev/null | awk '/default/ { print $3; exit }'
}

get_current_signal() {
    nmcli -t -f IN-USE,SIGNAL device wifi list 2>/dev/null \
        | awk -F: '$1 == "*" { print $2; exit }'
}

# --- Contrôle qualité de la connexion active (anti "wifi zombie") ----------

check_connection_quality() {
    local signal gateway
    signal="$(get_current_signal)"
    if [ -z "$signal" ] || [ "$signal" -lt "$WIFI_SIGNAL_MIN" ]; then
        log WARNING "Contrôle qualité échoué : signal trop faible (${signal:-inconnu}% < ${WIFI_SIGNAL_MIN}%)."
        return 1
    fi
    gateway="$(get_gateway_ip)"
    if [ -z "$gateway" ] || ! ping -c 1 -W 2 "$gateway" >/dev/null 2>&1; then
        log WARNING "Contrôle qualité échoué : passerelle injoignable (${gateway:-inconnue})."
        return 1
    fi
    return 0
}

# --- Sélection et connexion aux réseaux connus -----------------------------

list_candidate_networks() {
    local known_profiles
    known_profiles="$(nmcli -t -f NAME connection show 2>/dev/null)"

    nmcli -t -f SSID,SIGNAL device wifi list --rescan yes 2>/dev/null \
        | awk -F: '$1 != ""' \
        | sort -t: -k2 -rn \
        | awk -F: '!seen[$1]++' \
        | while IFS=: read -r ssid signal; do
            [ -n "$signal" ] && [ "$signal" -ge "$WIFI_SIGNAL_MIN" ] || continue
            printf '%s\n' "$known_profiles" | grep -Fxq "$ssid" || continue
            is_blacklisted "$ssid" && continue
            printf '%s\n' "$ssid"
        done
}

stop_ap_if_active() {
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fxq "$AP_CONNECTION_NAME"; then
        log INFO "Coupure du point d'accès (une tentative de connexion réelle commence)."
        nmcli connection down "$AP_CONNECTION_NAME" >/dev/null 2>&1
    fi
}

try_connect_known_networks() {
    local candidates ssid found=1
    candidates="$(list_candidate_networks)"
    [ -n "$candidates" ] || return 1

    stop_ap_if_active
    while IFS= read -r ssid; do
        [ -n "$ssid" ] || continue
        log INFO "Tentative de connexion à '$ssid' (profil connu, signal visible)..."
        if timeout "$CONNECT_TIMEOUT_SEC" nmcli connection up "$ssid" >/dev/null 2>&1; then
            log INFO "Connecté à '$ssid'."
            write_fail_count 0
            found=0
            break
        fi
        log WARNING "Échec de connexion à '$ssid'."
    done <<< "$candidates"
    return "$found"
}

# --- Point d'accès de secours -----------------------------------------------

ensure_ap_profile_exists() {
    if ! nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$AP_CONNECTION_NAME"; then
        log INFO "Création du profil AP '$AP_CONNECTION_NAME' (SSID '$AP_SSID')."
        nmcli connection add type wifi ifname "*" con-name "$AP_CONNECTION_NAME" autoconnect no \
            ssid "$AP_SSID" mode ap 802-11-wireless.band bg \
            ipv4.method shared ipv4.addresses "${AP_IP}/24" >/dev/null 2>&1
        nmcli connection modify "$AP_CONNECTION_NAME" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$AP_PASSWORD" \
            >/dev/null 2>&1
    fi
}

start_ap() {
    ensure_ap_profile_exists
    log WARNING "Aucun réseau connu joignable : repli sur le point d'accès '$AP_SSID' (IP ${AP_IP})."
    nmcli connection up "$AP_CONNECTION_NAME" >/dev/null 2>&1
}

# --- Point d'entrée ---------------------------------------------------------

main() {
    rotate_log_if_needed

    local active_conn
    active_conn="$(get_active_wifi_connection_name)"

    if [ -n "$active_conn" ] && [ "$active_conn" != "$AP_CONNECTION_NAME" ]; then
        if check_connection_quality; then
            write_fail_count 0
            return 0
        fi

        local fails
        fails=$(( $(read_fail_count) + 1 ))
        write_fail_count "$fails"
        log WARNING "Échec qualité n°${fails}/${WIFI_FAIL_THRESHOLD} pour '$active_conn'."

        if [ "$fails" -lt "$WIFI_FAIL_THRESHOLD" ]; then
            return 0
        fi

        log WARNING "Seuil d'échecs atteint pour '$active_conn' : déconnexion et blacklist temporaire."
        blacklist_ssid "$active_conn"
        nmcli connection down "$active_conn" >/dev/null 2>&1
        write_fail_count 0
        # On retente aussitôt ci-dessous (pas d'attente du prochain cycle).
    fi

    if try_connect_known_networks; then
        return 0
    fi

    start_ap
}

main "$@"
