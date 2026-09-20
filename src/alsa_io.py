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
from typing import List, Optional

import audio_io
import config

logger = logging.getLogger(__name__)

OUTPUT_LINEOUT = "lineout"
OUTPUT_HEADPHONE = "headphone"
OUTPUT_BOTH = "both"
OUTPUTS = (OUTPUT_LINEOUT, OUTPUT_HEADPHONE, OUTPUT_BOTH)

# Dernière sortie effectivement appliquée. Sur un parcours invité la séquence
# est lineout (sonnerie) -> headphone (message) -> headphone (bip) : le cache
# économise une commutation sur trois.
_last_output: Optional[str] = None
# L'avertissement « configuration ALSA indisponible » n'est émis qu'une fois,
# sinon c'est une ligne de log par lecture.
_unavailable_warned = False


def _script_env() -> dict:
    """Environnement du script : numéro de carte dérivé de SOUND_CARD."""
    env = dict(os.environ)
    env["ALSA_CARD"] = audio_io.card_index() or "1"
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
    global _last_output
    if mode not in OUTPUTS:
        logger.error("Sortie inconnue : %r (attendu %s)", mode, ", ".join(OUTPUTS))
        return False

    argv = [mode] if store else ["--no-store", mode]
    result = _run(argv, config.AUDIO_SETUP_TIMEOUT_SEC)
    if result is None:
        _last_output = None
        return False
    if result.returncode != 0:
        logger.warning("Configuration du codec en échec (code %s) : %s",
                       result.returncode, (result.stderr or "").strip())
        _last_output = None
        return False

    _last_output = mode
    logger.info("Codec configuré (sortie %s).", mode)
    return True


def select_output(output: str, force: bool = False) -> bool:
    """Commute le codec sur `output` avant une lecture (commutation rapide).

    Ne touche ni à l'entrée micro ni au routage, et n'écrit jamais
    asound.state : seuls les numids de sortie sont modifiés, soit quelques
    dizaines de millisecondes.

    Retourne True si la sortie est bien celle demandée (y compris quand rien
    n'a eu besoin d'être fait). Un échec est journalisé sans être propagé :
    l'appelant joue quand même le fichier.
    """
    global _last_output
    if output not in OUTPUTS:
        logger.error("Sortie inconnue : %r (attendu %s)", output, ", ".join(OUTPUTS))
        return False
    if not force and _last_output == output:
        return True

    result = _run([f"switch-{output}"], config.AUDIO_SWITCH_TIMEOUT_SEC)
    if result is None:
        _last_output = None
        return False
    if result.returncode != 0:
        logger.warning("Commutation vers %s en échec (code %s) : %s",
                       output, result.returncode, (result.stderr or "").strip())
        _last_output = None
        return False

    _last_output = output
    logger.debug("Sortie commutée sur %s.", output)
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
    global _last_output, _unavailable_warned
    _last_output = None
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
