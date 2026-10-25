"""Score de pertinence d'un message d'invité, sur 10 (page /messages).

Calculé sur le fichier post-traité, celui qu'on écoute : sur le brut, le
ronflement secteur (retiré par le traitement) ferait passer le bruit de fond
pour bien plus fort qu'il n'est.

1. **durée** < SCORE_DUREE_MIN_SEC : pas de texte, score 0 ;
2. **rapport signal sur bruit** : écart entre la voix (95e centile des niveaux
   de blocs de 20 ms) et le bruit de fond (20e centile), les mêmes mesures que
   le traitement ; SCORE_RSB_MIN_DB donne 0, SCORE_RSB_MAX_DB donne 10,
   linéaire entre les deux ;
3. **part de parole** : proportion de blocs à plus de SCORE_SEUIL_PAROLE_DB
   au-dessus du bruit. Sous SCORE_PART_PAROLE_MIN, la note est réduite d'autant
   (moitié moins de parole que le seuil -> note divisée par deux) ;
4. **message long** (> SCORE_DUREE_LONGUE_SEC) : souvent du blanc, note
   multipliée par SCORE_FACTEUR_LONG.

mesurer() lit le fichier, noter() n'est qu'une formule : les deux sont
séparés pour être testés chacun de son côté. Mesures et score sont gardés
dans l'index des messages (messages_index.py).
"""

import wave
from pathlib import Path
from typing import Dict

import config
import traitement_audio

# Même pas que le traitement : les deux parlent des mêmes blocs.
BLOC_MS = traitement_audio.BLOC_MS


class ErreurScore(Exception):
    pass


def mesurer(path: Path) -> Dict[str, float]:
    """Durée, RSB et part de parole d'un message ; lève ErreurScore s'il est illisible."""
    np = traitement_audio.np
    if np is None:
        raise ErreurScore(f"numpy non importable ({traitement_audio._NUMPY_ERREUR})")
    try:
        d, sr = traitement_audio.charger(Path(path))
    except (OSError, wave.Error, EOFError, ValueError, traitement_audio.ErreurTraitement) as exc:
        raise ErreurScore(str(exc)) from exc

    duree = len(d) / sr if sr else 0.0
    # La première demi-seconde porte la charge de l'électret : écartée, comme
    # pour l'estimation du bruit au traitement.
    debut = int(config.TRAITEMENT_PROFIL_DEBUT_SEC * sr)
    niveaux = traitement_audio.niveaux_blocs(
        d[debut:], traitement_audio._echantillons(BLOC_MS, sr))
    if len(niveaux) == 0:
        return {"duree_sec": round(duree, 2), "rsb_db": 0.0, "part_parole": 0.0}

    voix = float(np.percentile(niveaux, 95))
    bruit = float(np.percentile(niveaux, 20))
    parole = float((niveaux > bruit + config.SCORE_SEUIL_PAROLE_DB).mean())
    return {
        "duree_sec": round(duree, 2),
        "rsb_db": round(voix - bruit, 1),
        "part_parole": round(parole, 3),
    }


def noter(mesures: Dict[str, float]) -> float:
    """Note sur 10, arrondie au dixième, à partir des mesures de mesurer()."""
    duree = mesures.get("duree_sec", 0.0)
    if duree < config.SCORE_DUREE_MIN_SEC:
        return 0.0
    etendue = config.SCORE_RSB_MAX_DB - config.SCORE_RSB_MIN_DB
    rsb = (mesures.get("rsb_db", 0.0) - config.SCORE_RSB_MIN_DB) / etendue if etendue > 0 else 1.0
    note = 10.0 * min(max(rsb, 0.0), 1.0)
    part = mesures.get("part_parole", 0.0)
    if config.SCORE_PART_PAROLE_MIN > 0 and part < config.SCORE_PART_PAROLE_MIN:
        note *= part / config.SCORE_PART_PAROLE_MIN
    if duree > config.SCORE_DUREE_LONGUE_SEC:
        note *= config.SCORE_FACTEUR_LONG
    return round(note, 1)
