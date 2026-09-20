#!/bin/bash
#
# IQaudio Codec Zero (DA7213) — Pi Zero 2 W "livredor"
# Micro ADA1063 sur Aux gauche -> capture mono dupliquee en stereo
# Sortie selectionnable : casque (defaut) / line out / les deux
#
# Usage :
#   ./audio-setup.sh              -> config complete, sortie casque
#   ./audio-setup.sh headphone    -> idem
#   ./audio-setup.sh lineout      -> config complete, sortie line out
#   ./audio-setup.sh both         -> config complete, les deux sorties
#   ./audio-setup.sh status       -> etat courant, ne modifie rien
#   ./audio-setup.sh --no-store <sortie>   -> ne pas ecrire asound.state
#
#   ./audio-setup.sh switch-headphone      -> bascule de sortie seule (rapide)
#   ./audio-setup.sh switch-lineout        -> idem, vers le line out
#   ./audio-setup.sh switch-both           -> idem, les deux sorties
#
# Les modes switch-* ne touchent ni a l'entree ni au routage et n'ecrivent
# jamais asound.state : c'est ce que src/alsa_io.py appelle avant chaque
# lecture pour envoyer la sonnerie sur le line out et le reste sur le casque.
#
# Cablage du livre d'or (cf. README, "Sorties audio") :
#   - line out   -> 1 haut-parleur mono de sonnerie, qui lit la piste gauche
#   - headphone  -> ecouteur du combine (L) + ecouteur secondaire (R)
# Les fichiers joues sont donc stereo avec les deux pistes identiques.
#
# Numero de carte : variable d'environnement ALSA_CARD (defaut 1), pour
# rester aligne sur config.SOUND_CARD sans dupliquer la valeur.
#
# Reglages issus de la session du 19/09/2026 :
#   - numid=78 (AUX Jack Switch) indispensable, sinon pas d'horloge I2S
#   - numid=3 a 49 : optimum trimmer ADA1063 (butee horaire) / preampli codec
#     Plancher mesure -42 dBFS, crete parole ~0,29 -> ecart 31 dB
#   - ALC (numid=60) imperativement off : sature l'entree en l'absence de signal
#

set -u

CARD="${ALSA_CARD:-1}"
STORE=1
FAILED=0

# ---------------------------------------------------------------- utilitaires

set_ctl() {
    # $1 = numid, $2 = valeur
    if ! amixer -c "$CARD" cset numid="$1" "$2" >/dev/null 2>&1; then
        echo "  ! echec numid=$1 -> $2" >&2
        # Un cset rate signale un numid qui a glisse (mise a jour de driver) :
        # le script doit sortir en erreur pour que l'appelant le sache.
        FAILED=1
        return 1
    fi
}

get_ctl() {
    amixer -c "$CARD" cget numid="$1" 2>/dev/null | grep -o ': values=.*' | cut -d= -f2
}

die() {
    echo "Erreur : $*" >&2
    exit 1
}

# ---------------------------------------------------------- verifications

check_card() {
    if ! amixer -c "$CARD" info >/dev/null 2>&1; then
        die "carte $CARD introuvable. Verifier : cat /proc/asound/cards"
    fi
    local name
    name=$(amixer -c "$CARD" info 2>/dev/null | grep -o "'.*'" | head -1)
    case "$name" in
        *IQaudIOCODEC*) ;;
        *) echo "Attention : carte $CARD = $name, IQaudIOCODEC attendu" >&2 ;;
    esac
}

# -------------------------------------------------------------- chaine micro

setup_input() {
    echo "Entree  : Aux L (ADA1063)"

    set_ctl 78 on           # AUX Jack Switch — alimente le chemin DAPM
    set_ctl 25 on,on        # Aux Switch
    set_ctl 3  49,49        # Aux Volume            +6 dB
    set_ctl 81 on           # Mixin Left  <- Aux Left
    set_ctl 26 on,on        # Mixin PGA Switch
    set_ctl 4  6,6          # Mixin PGA Volume      +4,5 dB
    set_ctl 27 on,on        # ADC Switch
    set_ctl 5  112,112      # ADC Volume             0 dB
    set_ctl 15 on           # ADC HPF Switch
    set_ctl 16 0            # ADC HPF Cutoff Fs/24000
    set_ctl 60 off,off      # ALC off — sinon saturation du plancher
}

disable_unused_inputs() {
    set_ctl 23 off          # Mic 1
    set_ctl 24 off          # Mic 2
    set_ctl 77 off          # Onboard MIC
    set_ctl 76 off          # MIC Jack Switch
    set_ctl 59 off,off      # DMIC
    set_ctl 82 off          # Mixin Left  Mic 1
    set_ctl 83 off          # Mixin Left  Mic 2
    set_ctl 85 off          # Mixin Right <- Aux Right
    set_ctl 86 off          # Mixin Right Mic 2
    set_ctl 87 off          # Mixin Right Mic 1
    set_ctl 84 off          # Mixin L <- Mixin R
    set_ctl 88 off          # Mixin R <- Mixin L
}

# ------------------------------------------------------- duplication mono

setup_routing() {
    echo "Routage : mono L -> stereo"

    set_ctl 89 0            # DAI Left  <- ADC Left      (capture)
    set_ctl 90 0            # DAI Right <- ADC Left      (capture)
    set_ctl 91 2            # DAC Left  <- DAI Input Left  (lecture)
    set_ctl 92 2            # DAC Right <- DAI Input Left  (lecture)

    set_ctl 6   112,112     # DAC Volume             0 dB
    set_ctl 96  on          # Mixout Left  <- DAC Left
    set_ctl 103 on          # Mixout Right <- DAC Right

    # Pas de bouclage interne entree -> sortie
    set_ctl 93  off         # Mixout Left  <- Aux Left
    set_ctl 100 off         # Mixout Right <- Aux Right
    set_ctl 94  off         # Mixout Left  <- Mixin Left
    set_ctl 95  off         # Mixout Left  <- Mixin Right
    set_ctl 101 off         # Mixout Right <- Mixin Right
    set_ctl 102 off         # Mixout Right <- Mixin Left
}

# ------------------------------------------------------------- sorties

output_headphone() {
    set_ctl 29 off          # Lineout off
    set_ctl 75 on           # HP Jack Switch
    set_ctl 28 on,on        # Headphone on
    set_ctl 7  57,57        # Headphone Volume       0 dB
    echo "Sortie  : casque"
}

output_lineout() {
    set_ctl 28 off,off      # Headphone off
    set_ctl 29 on           # Lineout on
    set_ctl 8  48           # Lineout Volume         0 dB
    echo "Sortie  : line out"
}

output_both() {
    set_ctl 75 on           # HP Jack Switch
    set_ctl 28 on,on        # Headphone on
    set_ctl 7  57,57        # Headphone Volume       0 dB
    set_ctl 29 on           # Lineout on
    set_ctl 8  48           # Lineout Volume         0 dB
    echo "Sortie  : casque + line out"
}

# -------------------------------------------------------------- etat

show_status() {
    echo "IQaudio Codec Zero — carte $CARD"
    echo
    echo "Entree"
    printf "  %-22s %s\n" "AUX Jack Switch"   "$(get_ctl 78)"
    printf "  %-22s %s\n" "Aux Switch"        "$(get_ctl 25)"
    printf "  %-22s %s\n" "Aux Volume"        "$(get_ctl 3)"
    printf "  %-22s %s\n" "Mixin PGA Volume"  "$(get_ctl 4)"
    printf "  %-22s %s\n" "ADC Volume"        "$(get_ctl 5)"
    printf "  %-22s %s\n" "ADC HPF"           "$(get_ctl 15)"
    printf "  %-22s %s\n" "ALC Switch"        "$(get_ctl 60)"
    echo
    echo "Routage"
    printf "  %-22s %s\n" "DAI L / DAI R"     "$(get_ctl 89) / $(get_ctl 90)"
    printf "  %-22s %s\n" "DAC L / DAC R"     "$(get_ctl 91) / $(get_ctl 92)"
    printf "  %-22s %s\n" "Mixout L / R"      "$(get_ctl 96) / $(get_ctl 103)"
    echo
    echo "Sortie"
    printf "  %-22s %s\n" "Headphone Switch"  "$(get_ctl 28)"
    printf "  %-22s %s\n" "Headphone Volume"  "$(get_ctl 7)"
    printf "  %-22s %s\n" "Lineout Switch"    "$(get_ctl 29)"
    printf "  %-22s %s\n" "Lineout Volume"    "$(get_ctl 8)"
    echo

    local alc hp lo
    alc=$(get_ctl 60); hp=$(get_ctl 28); lo=$(get_ctl 29)
    case "$alc" in
        off,off) ;;
        *) echo "  ! ALC actif — cause connue de saturation du plancher" ;;
    esac
    if [ "$hp" = "off,off" ] && [ "$lo" = "off" ]; then
        echo "  ! aucune sortie active"
    fi

    # Ligne analysable par src/alsa_io.current_output(), a garder en dernier.
    local courant="inconnu"
    if [ "$hp" != "off,off" ] && [ "$lo" = "on" ]; then
        courant="both"
    elif [ "$hp" != "off,off" ]; then
        courant="headphone"
    elif [ "$lo" = "on" ]; then
        courant="lineout"
    fi
    echo "output=$courant"
}

usage() {
    cat <<'EOF'
Usage : audio-setup.sh [--no-store] {headphone|lineout|both|status|switch-*}

  headphone         config complete, sortie casque      (defaut)
  lineout           config complete, sortie line out
  both              config complete, les deux sorties
  status            affiche l'etat courant, ne modifie rien

  switch-headphone  bascule de sortie seule, sans toucher entree/routage
  switch-lineout    idem, vers le line out
  switch-both       idem, les deux sorties

  --no-store        ne pas sauvegarder dans asound.state

Variable d'environnement : ALSA_CARD (numero de carte, defaut 1).
EOF
}

# ---------------------------------------------------------------- main

while [ $# -gt 0 ]; do
    case "$1" in
        --no-store) STORE=0; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          break ;;
    esac
done

MODE="${1:-headphone}"

check_card

case "$MODE" in
    status)
        show_status
        exit 0
        ;;
    headphone|lineout|both)
        setup_input
        disable_unused_inputs
        setup_routing
        case "$MODE" in
            headphone) output_headphone ;;
            lineout)   output_lineout   ;;
            both)      output_both      ;;
        esac
        ;;
    switch-headphone|switch-lineout|switch-both)
        # Bascule rapide : uniquement les numids de sortie (28/29/75/7/8).
        # Appelee avant chaque lecture, elle ne doit rien reconfigurer
        # d'autre ni ecrire asound.state.
        STORE=0
        case "$MODE" in
            switch-headphone) output_headphone ;;
            switch-lineout)   output_lineout   ;;
            switch-both)      output_both      ;;
        esac
        ;;
    *)
        usage
        exit 1
        ;;
esac

if [ "$STORE" -eq 1 ]; then
    if [ "$(id -u)" -eq 0 ]; then
        alsactl store "$CARD" && echo "Etat sauvegarde (asound.state)"
    elif sudo -n true 2>/dev/null; then
        sudo alsactl store "$CARD" && echo "Etat sauvegarde (asound.state)"
    else
        # Le service tourne sous un utilisateur non privilegie : ce n'est pas
        # une erreur, asound.state est fige une fois pour toutes par
        # scripts/install.sh.
        echo "Etat non sauvegarde : lancer 'sudo alsactl store $CARD' pour figer"
    fi
fi

exit "$FAILED"
