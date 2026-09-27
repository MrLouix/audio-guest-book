# Fonctions communes aux scripts d'installation systemd (à sourcer, pas à
# exécuter). Attend PROJECT_DIR défini par l'appelant.
#
# Les unités du dépôt sont écrites pour l'utilisateur `pi` et le chemin
# /home/pi/livre_dor (spec §8). Les images Raspberry Pi OS récentes ne
# créent plus l'utilisateur `pi` : on garde les unités symlinkées telles
# quelles et on les adapte via un drop-in systemd
# (/etc/systemd/system/<unité>.d/local.conf) qui remplace User=,
# WorkingDirectory= et ExecStart= par l'utilisateur et le chemin réels.

SPEC_DIR=/home/pi/livre_dor

# Utilisateur d'exécution : $LIVRE_DOR_USER, sinon propriétaire du dépôt.
resolve_run_user() {
    RUN_USER="${LIVRE_DOR_USER:-$(stat -c %U "$PROJECT_DIR")}"
    if [ "$RUN_USER" = "root" ]; then
        echo "Le dépôt appartient à root : précisez l'utilisateur avec LIVRE_DOR_USER=<nom> sudo -E $0" >&2
        exit 1
    fi
    if ! id "$RUN_USER" >/dev/null 2>&1; then
        echo "Utilisateur '$RUN_USER' introuvable (LIVRE_DOR_USER ?)." >&2
        exit 1
    fi
}

# Symlink d'une unité + drop-in d'adaptation utilisateur/chemin (services).
install_unit() {
    local unit="$1"
    local src="$PROJECT_DIR/systemd/$unit"
    local dropin_dir="/etc/systemd/system/$unit.d"

    ln -sf "$src" "/etc/systemd/system/$unit"

    case "$unit" in *.service) ;; *) return 0 ;; esac

    local lines
    lines="$(grep -E '^(User|WorkingDirectory|ExecStart)=' "$src" \
        | sed -e "s#$SPEC_DIR#$PROJECT_DIR#g" -e "s#^User=.*#User=$RUN_USER#")"
    if [ -z "$lines" ]; then
        rm -rf "$dropin_dir"
        return 0
    fi

    mkdir -p "$dropin_dir"
    {
        echo "# Généré par scripts/lib_systemd.sh — relancer le script d'installation pour régénérer."
        echo "[Service]"
        # ExecStart= vide d'abord : réinitialise la commande de l'unité de base.
        if grep -q '^ExecStart=' <<<"$lines"; then echo "ExecStart="; fi
        echo "$lines"
    } > "$dropin_dir/local.conf"
}
