#!/bin/bash
#
# IQaudio Codec Zero (DA7213) — Pi Zero 2 W "livredor"
# Micro electret sur le jack MIC (Mic 1, bias interne du DA7213)
#   -> capture mono dupliquee en stereo, a enregistrer en 16 kHz
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
#   ./audio-setup.sh switch-lineout 60     -> idem, volume a 60 %
#
# Les modes switch-* ne touchent ni a l'entree ni au routage et n'ecrivent
# jamais asound.state : c'est ce que src/alsa_io.py appelle avant chaque
# lecture pour envoyer la sonnerie sur le line out et le reste sur le casque.
# Le second argument, facultatif, est le volume en pourcentage (0-100) du
# niveau maximum de la sortie (defaut 100). Il est proportionnel a la valeur
# brute du controle, donc lineaire en dB entre le plancher du controle et ce
# maximum : a 50 %, un line out plafonne a 0 dB sort a -24 dB. 0 coupe la
# sortie (switch off).
#
# Niveaux maximum (ce que vaut 100 %), en dB, par variables d'environnement
# — fixees par src/alsa_io.py depuis config.AUDIO_MAX_DB_* :
#   ALSA_HP_MAX_DB  casque   entier -57..+6  (defaut +6, valeur brute 63)
#   ALSA_LO_MAX_DB  line out entier -48..+15 (defaut 0,  valeur brute 48)
# Les defauts reproduisent les reglages du banc.
#
# Cablage du livre d'or (cf. README, "Sorties audio") :
#   - line out   -> 1 haut-parleur mono de sonnerie, qui lit la piste gauche
#   - headphone  -> ecouteur du combine (L) + ecouteur secondaire (R)
# Les fichiers joues sont donc stereo avec les deux pistes identiques.
#
# Numero de carte : variable d'environnement ALSA_CARD (defaut 1), pour
# rester aligne sur config.SOUND_CARD sans dupliquer la valeur.
#
# Reglages issus du banc du 22 au 25/09/2026 (voir docs/banc_audio/) :
#   - numid=76 (MIC Jack Switch) indispensable : chemin DAPM complet
#   - numid=79 (Mic 1 Amp Source MUX) sur MIC_P (1) : Differential (0)
#     perd 22 dB de signal, MIC_N (2) ne capte rien
#   - gain total 24 dB (Mic 1 +24, PGA 0), abaisse le 02/10/2026 depuis
#     42 dB : telephone monte, la voix ecretait franchement (8 a 15 % des
#     tranches de 100 ms a pleine echelle sur des messages normaux, jusqu'a
#     60 % en parlant fort), ecretage irreparable au traitement. Baisser le
#     gain ne coute rien en SNR : le hum 50 Hz et le souffle suivent le gain
#     au dB pres (ils entrent sur la ligne micro), et le plancher reste vers
#     -70 dBFS, loin des -98 dB du 16 bits. La normalisation de
#     src/traitement_audio.py remonte ensuite la voix a -20 dBFS. C'est le
#     preampli Mic 1 qu'on baisse en premier, pour qu'il ne sature pas
#     lui-meme avant le PGA. Reglage fin : PGA par pas de 1,5 dB
#   - HPF du codec garde on (anti-DC) : coupure max Fs/3000, inutile contre
#     le 50 Hz, traite en logiciel par src/traitement_audio.py
#   - ALC (numid=60) imperativement off : sature l'entree en l'absence de signal
#   - le micro MEMS embarque est coupe des qu'une fiche est dans le jack MIC
#
# Echelles dB :
#   Mic 1 Volume (numid=1) : dB = -6   + v * 6.0    (v 0-7)
#   Mixin PGA    (numid=4) : dB = -4.5 + v * 1.5    (v 0-15)
#   ADC / DAC  (numid=5/6) : dB = -78  + (v-8)*0.75 -> 0 dB = 112
#   Headphone    (numid=7) : dB = -57  + v * 1.0    -> 0 dB = 57 (v 0-63)
#   Lineout      (numid=8) : dB = -48  + v * 1.0    -> 0 dB = 48 (v 0-63)
#   (echelles TLV du driver da7213 ; a verifier sur le Pi par
#    `amixer -c 1 cget numid=7` et `numid=8`, ligne dBscale-min)
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
    echo "Entree  : jack MIC (electret, Mic 1)"

    set_ctl 76 on           # MIC Jack Switch — alimente le chemin DAPM
    set_ctl 23 on           # Mic 1 Switch
    set_ctl 79 1            # Mic 1 Amp Source MUX -> MIC_P
    set_ctl 1  5            # Mic 1 Volume         +24 dB
    set_ctl 82 on           # Mixin Left  <- Mic 1
    set_ctl 87 on           # Mixin Right <- Mic 1
    set_ctl 26 on,on        # Mixin PGA Switch
    set_ctl 4  3,3          # Mixin PGA Volume       0 dB (total 24 dB)
    set_ctl 27 on,on        # ADC Switch
    set_ctl 5  112,112      # ADC Volume             0 dB
    set_ctl 15 on           # ADC HPF Switch (anti-DC)
    set_ctl 16 0            # ADC HPF Cutoff Fs/24000
    set_ctl 17 off          # ADC Voice Mode — aucun gain mesurable
    set_ctl 60 off,off      # ALC off — sinon saturation du plancher
}

disable_unused_inputs() {
    set_ctl 78 off          # AUX Jack Switch
    set_ctl 25 off,off      # Aux Switch
    set_ctl 81 off          # Mixin Left  <- Aux Left
    set_ctl 85 off          # Mixin Right <- Aux Right
    set_ctl 77 off          # Onboard MIC (MEMS)
    set_ctl 24 off          # Mic 2
    set_ctl 59 off,off      # DMIC
    set_ctl 83 off          # Mixin Left  Mic 2
    set_ctl 86 off          # Mixin Right Mic 2
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

# Echelles des controles de sortie (dB = MIN + v, v 0-63).
HP_DB_MIN=-57; HP_DB_MAX=6
LO_DB_MIN=-48; LO_DB_MAX=15
# Niveaux maximum (100 %), cf. en-tete ; valides dans check_levels.
HP_MAX_DB="${ALSA_HP_MAX_DB:-6}"
LO_MAX_DB="${ALSA_LO_MAX_DB:-0}"
HP_VOL_REF=63               # recalcules par check_levels
LO_VOL_REF=48
VOLUME=100                  # pourcentage, cf. en-tete

check_levels() {
    # $1 = nom, $2 = valeur, $3 = min, $4 = max
    case "$2" in
        ''|-|*[!0-9-]*|?*-*) die "$1 invalide : '$2' (entier $3..$4 attendu)" ;;
    esac
    if [ "$2" -lt "$3" ] || [ "$2" -gt "$4" ]; then
        die "$1 hors bornes : $2 (entier $3..$4 attendu)"
    fi
}

compute_refs() {
    check_levels ALSA_HP_MAX_DB "$HP_MAX_DB" "$HP_DB_MIN" "$HP_DB_MAX"
    check_levels ALSA_LO_MAX_DB "$LO_MAX_DB" "$LO_DB_MIN" "$LO_DB_MAX"
    HP_VOL_REF=$(( HP_MAX_DB - HP_DB_MIN ))
    LO_VOL_REF=$(( LO_MAX_DB - LO_DB_MIN ))
}

hp_on() {
    local v=$(( HP_VOL_REF * VOLUME / 100 ))
    set_ctl 75 on           # HP Jack Switch
    if [ "$VOLUME" -eq 0 ]; then
        set_ctl 28 off,off  # volume 0 : casque coupe
    else
        set_ctl 28 on,on    # Headphone on
    fi
    set_ctl 7  "$v,$v"      # Headphone Volume
}

lo_on() {
    local v=$(( LO_VOL_REF * VOLUME / 100 ))
    if [ "$VOLUME" -eq 0 ]; then
        set_ctl 29 off      # volume 0 : line out coupe
    else
        set_ctl 29 on       # Lineout on
    fi
    set_ctl 8  "$v"         # Lineout Volume
}

output_headphone() {
    set_ctl 29 off          # Lineout off
    hp_on
    echo "Sortie  : casque ($VOLUME % de $HP_MAX_DB dB)"
}

output_lineout() {
    set_ctl 28 off,off      # Headphone off
    lo_on
    echo "Sortie  : line out ($VOLUME % de $LO_MAX_DB dB)"
}

output_both() {
    hp_on
    lo_on
    echo "Sortie  : casque + line out ($VOLUME % de $HP_MAX_DB / $LO_MAX_DB dB)"
}

# -------------------------------------------------------------- etat

show_status() {
    echo "IQaudio Codec Zero — carte $CARD"
    echo
    echo "Entree"
    printf "  %-22s %s\n" "MIC Jack Switch"   "$(get_ctl 76)"
    printf "  %-22s %s\n" "Mic 1 Switch"      "$(get_ctl 23)"
    printf "  %-22s %s\n" "Mic 1 MUX (1=P)"   "$(get_ctl 79)"
    printf "  %-22s %s\n" "Mic 1 Volume"      "$(get_ctl 1)"
    printf "  %-22s %s\n" "Mixin PGA Volume"  "$(get_ctl 4)"
    printf "  %-22s %s\n" "ADC Volume"        "$(get_ctl 5)"
    printf "  %-22s %s\n" "ADC HPF"           "$(get_ctl 15)"
    printf "  %-22s %s\n" "ADC Voice Mode"    "$(get_ctl 17)"
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
  switch-* VOLUME   idem, volume en % du niveau maximum (0-100, 0 = coupe)

  --no-store        ne pas sauvegarder dans asound.state

Variables d'environnement :
  ALSA_CARD       numero de carte (defaut 1)
  ALSA_HP_MAX_DB  niveau maximum du casque, dB entier -57..+6 (defaut 6)
  ALSA_LO_MAX_DB  niveau maximum du line out, dB entier -48..+15 (defaut 0)
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

compute_refs
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
        if [ $# -ge 2 ]; then
            case "$2" in
                ''|*[!0-9]*) die "volume invalide : '$2' (entier 0-100 attendu)" ;;
            esac
            [ "$2" -le 100 ] || die "volume invalide : '$2' (entier 0-100 attendu)"
            VOLUME=$(( 10#$2 ))
        fi
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
