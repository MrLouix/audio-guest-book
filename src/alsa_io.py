"""Configuration et commutation des sorties du codec (IQaudio Codec Zero, §4.1).

Le câblage du livre d'or sépare les deux sorties du DA7213 :

- **line out** : un seul haut-parleur mono, celui de la sonnerie, qui lit la
  piste gauche ;
- **headphone** : l'écouteur du combiné (L) et un écouteur secondaire (R).

Le codec ne peut pas alimenter les deux indépendamment : il faut le commuter
sur la bonne sortie *avant* chaque lecture — line out pour la sonnerie, casque
pour tout le reste.

Tous les réglages du codec (numids `amixer`) vivent dans
`scripts/audio-setup.sh` et **nulle part ailleurs** : un numid n'est qu'un
index dans l'énumération des contrôles ALSA, qui peut glisser d'une version de
driver à l'autre. Les dupliquer ici garantirait qu'un jour les deux versions
divergent et que le téléphone sonne dans l'écouteur. Ce module ne connaît que
trois mots : `lineout`, `headphone`, `both`.

Aucune fonction ne lève : sur un poste de développement (ni carte, ni
`amixer`) comme sur un Pi dont le mixer est momentanément grognon, un échec de
commutation est journalisé puis ignoré. Le mauvais haut-parleur vaut toujours
mieux que le silence en plein mariage (§7.4).
"""

import logging
import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import audio_io
import config

logger = logging.getLogger(__name__)

OUTPUT_LINEOUT = "lineout"
OUTPUT_HEADPHONE = "headphone"
OUTPUT_BOTH = "both"
OUTPUTS = (OUTPUT_LINEOUT, OUTPUT_HEADPHONE, OUTPUT_BOTH)

VOLUME_MAX = 100

# Dernière sortie effectivement appliquée, et son volume. Sur un parcours
# invité la séquence est lineout (sonnerie) -> headphone (message) ->
# headphone (bip) : le cache économise une commutation sur trois.
_last_output: Optional[str] = None
_last_volume: Optional[int] = None
# Plafonds en dB (casque, line out) en vigueur lors de cette commutation :
# ils se règlent à chaud, et un changement doit forcer la commutation
# suivante même si sortie et volume sont inchangés.
_last_levels: Optional[Tuple[int, int]] = None
# L'avertissement « configuration ALSA indisponible » n'est émis qu'une fois,
# sinon c'est une ligne de log par lecture.
_unavailable_warned = False


def max_levels() -> Tuple[int, int]:
    """Niveaux maximum (dB) du casque et du line out, lus à chaque appel (réglage à chaud)."""
    return int(config.AUDIO_MAX_DB_CASQUE), int(config.AUDIO_MAX_DB_LINEOUT)


def _script_env() -> dict:
    """Environnement du script : numéro de carte dérivé de SOUND_CARD, plafonds en dB."""
    env = dict(os.environ)
    env["ALSA_CARD"] = audio_io.card_index() or "1"
    casque, lineout = max_levels()
    env["ALSA_HP_MAX_DB"] = str(casque)
    env["ALSA_LO_MAX_DB"] = str(lineout)
    return env


def is_available() -> bool:
    """Le script et `amixer` sont-ils utilisables ici ? (jamais d'exception)"""
    global _unavailable_warned
    if not config.AUDIO_SETUP_SCRIPT.exists():
        raison = f"{config.AUDIO_SETUP_SCRIPT} introuvable"
    elif shutil.which("amixer") is None:
        raison = "amixer introuvable (paquet alsa-utils)"
    else:
        return True
    if not _unavailable_warned:
        logger.warning("Configuration ALSA indisponible (%s) : les sorties ne "
                       "seront pas commutées.", raison)
        _unavailable_warned = True
    return False


def _run(argv: List[str], timeout_sec: float) -> Optional[subprocess.CompletedProcess]:
    """Lance audio-setup.sh ; None si le script est indisponible ou n'a pas pu tourner."""
    if not is_available():
        return None
    cmd = ["bash", str(config.AUDIO_SETUP_SCRIPT), *argv]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False,
                              timeout=timeout_sec, env=_script_env())
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Échec de %s : %s", " ".join(cmd), exc)
        return None


def setup_card(mode: str = OUTPUT_HEADPHONE, store: bool = False) -> bool:
    """Configuration complète du codec : entrée micro, routage, sortie par défaut.

    Appelée une fois au démarrage du service. `store=False` par défaut :
    `alsactl store` écrit dans /var/lib/alsa/ et demande root, alors que le
    service tourne sous un utilisateur non privilégié — c'est
    `scripts/install.sh` qui fige l'état une fois pour toutes.
    """
    global _last_output, _last_volume, _last_levels
    if mode not in OUTPUTS:
        logger.error("Sortie inconnue : %r (attendu %s)", mode, ", ".join(OUTPUTS))
        return False

    argv = [mode] if store else ["--no-store", mode]
    result = _run(argv, config.AUDIO_SETUP_TIMEOUT_SEC)
    if result is None:
        _last_output = _last_volume = None
        return False
    if result.returncode != 0:
        logger.warning("Configuration du codec en échec (code %s) : %s",
                       result.returncode, (result.stderr or "").strip())
        _last_output = _last_volume = None
        return False

    # La configuration complète pose le niveau maximum (100 %).
    _last_output, _last_volume, _last_levels = mode, VOLUME_MAX, max_levels()
    logger.info("Codec configuré (sortie %s).", mode)
    return True


def _clamp_volume(volume: Optional[int]) -> int:
    """Volume en pourcentage borné à 0..100 ; None = niveau de référence."""
    if volume is None:
        return VOLUME_MAX
    try:
        return max(0, min(VOLUME_MAX, int(volume)))
    except (TypeError, ValueError):
        logger.warning("Volume illisible : %r, niveau de référence utilisé.", volume)
        return VOLUME_MAX


def select_output(output: str, force: bool = False, volume: Optional[int] = None) -> bool:
    """Commute le codec sur `output` avant une lecture (commutation rapide).

    Ne touche ni à l'entrée micro ni au routage, et n'écrit jamais
    asound.state : seuls les numids de sortie sont modifiés, soit quelques
    dizaines de millisecondes.

    `volume` est un pourcentage du niveau maximum de la sortie
    (config.AUDIO_MAX_DB_*, 0 = sortie coupée, None = 100) : c'est ainsi que la sonnerie et le combiné gardent chacun
    leur volume même s'ils partagent une sortie.

    Retourne True si la sortie est bien celle demandée (y compris quand rien
    n'a eu besoin d'être fait). Un échec est journalisé sans être propagé :
    l'appelant joue quand même le fichier.
    """
    global _last_output, _last_volume, _last_levels
    if output not in OUTPUTS:
        logger.error("Sortie inconnue : %r (attendu %s)", output, ", ".join(OUTPUTS))
        return False
    volume = _clamp_volume(volume)
    levels = max_levels()
    if (not force and _last_output == output and _last_volume == volume
            and _last_levels == levels):
        return True

    result = _run([f"switch-{output}", str(volume)], config.AUDIO_SWITCH_TIMEOUT_SEC)
    if result is None:
        _last_output = _last_volume = None
        return False
    if result.returncode != 0:
        logger.warning("Commutation vers %s en échec (code %s) : %s",
                       output, result.returncode, (result.stderr or "").strip())
        _last_output = _last_volume = None
        return False

    _last_output, _last_volume, _last_levels = output, volume, levels
    logger.debug("Sortie commutée sur %s (volume %d %%, plafonds casque %+d dB, "
                 "line out %+d dB).", output, volume, *levels)
    return True


def current_output() -> Optional[str]:
    """Sortie réellement active d'après `audio-setup.sh status` ; None si illisible.

    Lue depuis le matériel (et non depuis le cache) : c'est ce qu'affiche le
    dashboard, qui tourne dans un autre processus que livre_dor.py.
    """
    result = _run(["status"], config.AUDIO_SWITCH_TIMEOUT_SEC)
    if result is None or result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("output="):
            valeur = line.split("=", 1)[1].strip()
            return valeur if valeur in OUTPUTS else None
    return None


def invalidate_cache() -> None:
    """Oublie la dernière sortie appliquée (tests, ou reconfiguration externe)."""
    global _last_output, _last_volume, _last_levels, _unavailable_warned
    _last_output = _last_volume = _last_levels = None
    _unavailable_warned = False


def last_output() -> Optional[str]:
    """Dernière sortie appliquée par ce processus, sans interroger le matériel."""
    return _last_output


def _cli() -> None:
    """Test manuel : `python3 src/alsa_io.py lineout` ou `... status`."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Commutation des sorties du codec.")
    parser.add_argument("action", choices=[*OUTPUTS, "status", "setup"],
                        help="sortie à activer, 'setup' (config complète) ou 'status'")
    args = parser.parse_args()

    if args.action == "status":
        print(f"Sortie active : {current_output() or 'inconnue'}")
    elif args.action == "setup":
        print("OK" if setup_card() else "échec")
    else:
        print("OK" if select_output(args.action, force=True) else "échec")


if __name__ == "__main__":
    _cli()
