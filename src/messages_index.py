"""Index des messages : statut et score de chaque message (page /messages).

Le tri des messages après l'événement ne touche **jamais** aux fichiers
audio : ni suppression, ni renommage, ni régénération. Tout tient dans
`audio_src/index_messages.json`, seul endroit où l'on écrit :

    {
      "version": 1,
      "messages": {
        "message_2026-06-20_14-00-00.wav": {
          "statut": "conserve",              # a_trier | conserve | supprime
          "statut_maj": "2026-10-25T14:03:12Z",
          "score": 7.4,
          "duree_sec": 42.3, "rsb_db": 24.1, "part_parole": 0.62,
          "empreinte": "2764844:3f2a…"       # fichier sur lequel le score a été calculé
        }
      }
    }

Un message absent de l'index est « à trier ». Le mode restitution (§5.7) lit
tous les messages sauf ceux marqués « supprime » ; index absent ou illisible
-> tous lus (noms_supprimes()).

**Pourquoi audio_src/** : c'est le seul dossier synchronisé dans les deux sens
avec le Drive (§5.4). Le tri peut donc se faire sur le Pi comme sur un PC
(tri_messages.py, sur les dossiers synchronisés par Google Drive pour
ordinateur), et chacun retrouve les choix de l'autre.

**Conflits** : si le Pi et le PC modifient l'index entre deux synchros, rclone
bisync garde le plus récent et renomme l'autre, Google Drive pour ordinateur
crée « index_messages (1).json ». Toute copie de ce genre trouvée à côté de
l'index y est fusionnée message par message (le statut modifié le plus
récemment gagne), puis supprimée : aucun choix n'est perdu.

**Empreinte** : la taille et un condensé du début et de la fin du fichier,
identiques sur le Pi et sur le PC (la date de modification, elle, change à
chaque copie). Un score est recalculé quand le fichier n'a plus la même
empreinte, par exemple après un retraitement.
"""

import datetime
import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

import config
import fichiers
import score_message

logger = logging.getLogger(__name__)

VERSION = 1

STATUT_A_TRIER = "a_trier"
STATUT_CONSERVE = "conserve"
STATUT_SUPPRIME = "supprime"
STATUTS = (STATUT_A_TRIER, STATUT_CONSERVE, STATUT_SUPPRIME)

# Action de la page -> statut écrit dans l'index. « Supprimer » et
# « restaurer » ne sont que des statuts : aucun fichier n'est touché.
ACTIONS = {
    "conserver": STATUT_CONSERVE,
    "ne_plus_conserver": STATUT_A_TRIER,
    "supprimer": STATUT_SUPPRIME,
    "restaurer": STATUT_A_TRIER,
}

CHAMPS_SCORE = ("score", "duree_sec", "rsb_db", "part_parole", "empreinte")

EMPREINTE_OCTETS = 64 * 1024

RECORDING_NAME_RE = re.compile(r"^message_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_(\d+))?$")

# Lecture-modification-écriture de l'index : le dashboard sert plusieurs
# requêtes à la fois (threaded=True) et calcule les scores dans un thread.
# Réentrant : _charger_et_fusionner() est appelé verrou déjà pris.
_verrou = threading.RLock()


# --- Ordre chronologique (§5.7) ---------------------------------------------


def cle_chronologique(path: Path) -> tuple:
    """Clé de tri chronologique d'un enregistrement d'invité (§5.7).

    L'horodatage inscrit dans le nom du fichier par timestamped_recording_path()
    est la chronologie de référence : il voyage avec le fichier, contrairement à
    la date de modification, qu'une copie rclone ou une restauration de
    sauvegarde peut écraser. Le suffixe de collision _k désigne un
    enregistrement postérieur dans la même seconde, et se compare comme un
    entier (_2 avant _10). Repli sur la mtime pour un fichier au nom non
    conforme, ajouté à la main dans messages/.
    """
    match = RECORDING_NAME_RE.match(path.stem)
    if match:
        try:
            stamp = datetime.datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            stamp = None
        if stamp is not None:
            return (stamp.timestamp(), int(match.group(2) or 0), path.name)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, 0, path.name)


def messages_chronologiques(messages_dir: Path) -> List[Path]:
    """Tous les .wav de messages/, du plus ancien au plus récent ; [] si absent."""
    return sorted(fichiers.fichiers_wav(messages_dir), key=cle_chronologique)


# --- Lecture et écriture de l'index ------------------------------------------


def _entrees(data: dict) -> Dict[str, dict]:
    """Partie « messages » d'un index lu, sans les entrées mal formées (§7.4)."""
    messages = data.get("messages")
    if not isinstance(messages, dict):
        return {}
    return {nom: e for nom, e in messages.items() if isinstance(nom, str) and isinstance(e, dict)}


def lire(index_path: Optional[Path] = None) -> Dict[str, dict]:
    """Entrées de l'index ; {} s'il est absent ou illisible. N'écrit jamais rien."""
    return _entrees(fichiers.lire_json_dict(index_path or config.MESSAGES_INDEX_FILE))


def noms_supprimes(index_path: Optional[Path] = None) -> Set[str]:
    """Messages que la restitution ne doit pas lire ; vide si l'index est absent (§5.7)."""
    return {nom for nom, e in lire(index_path).items() if e.get("statut") == STATUT_SUPPRIME}


def _ecrire(index_path: Path, entrees: Dict[str, dict]) -> None:
    fichiers.ecrire_json_atomique(
        index_path, {"version": VERSION, "messages": dict(sorted(entrees.items()))})


def copies_en_conflit(index_path: Path) -> List[Path]:
    """Copies laissées par un conflit de synchro à côté de l'index.

    rclone bisync ajoute un suffixe au nom (index_messages.json.path1,
    index_messages.json.conflict1…), Google Drive pour ordinateur insère un
    numéro (index_messages (1).json) : on prend tout ce qui commence comme
    l'index, sauf l'index lui-même et son fichier temporaire d'écriture.
    """
    try:
        entrees = list(index_path.parent.iterdir())
    except OSError:
        return []
    return sorted(p for p in entrees
                  if p.name.startswith(index_path.stem) and p.name != index_path.name
                  and not p.name.endswith(".tmp") and p.is_file())


def fusionner(a: Dict[str, dict], b: Dict[str, dict]) -> Dict[str, dict]:
    """Fusion message par message de deux index.

    Statut : celui modifié le plus récemment (statut_maj, ISO 8601 UTC, se
    compare comme une chaîne). Score : celui de `a` s'il en a un, sinon celui
    de `b` — l'empreinte dira de toute façon s'il est encore valable.
    """
    resultat = {nom: dict(e) for nom, e in a.items()}
    for nom, eb in b.items():
        ea = resultat.setdefault(nom, {})
        plus_recent = str(eb.get("statut_maj", "")) > str(ea.get("statut_maj", ""))
        if "statut" in eb and ("statut" not in ea or plus_recent):
            ea["statut"] = eb["statut"]
            ea["statut_maj"] = eb.get("statut_maj", "")
        if "score" not in ea and "score" in eb:
            for champ in CHAMPS_SCORE:
                if champ in eb:
                    ea[champ] = eb[champ]
    return resultat


def _charger_et_fusionner(index_path: Path) -> Dict[str, dict]:
    """Index courant, copies en conflit fusionnées puis supprimées."""
    with _verrou:
        entrees = lire(index_path)
        copies = copies_en_conflit(index_path)
        if not copies:
            return entrees
        for copie in copies:
            data = fichiers.lire_json_dict(copie)
            if "messages" not in data:
                continue  # pas un index : on n'y touche pas
            entrees = fusionner(entrees, _entrees(data))
        try:
            _ecrire(index_path, entrees)
        except OSError as exc:
            logger.error("Index des messages : fusion non enregistrée (%s).", exc)
            return entrees
        for copie in copies:
            if "messages" in fichiers.lire_json_dict(copie):
                try:
                    copie.unlink()
                    logger.info("Index des messages : copie en conflit %s fusionnée.", copie.name)
                except OSError as exc:
                    logger.warning("Index des messages : %s non supprimée (%s).", copie.name, exc)
        return entrees


# --- Empreinte -----------------------------------------------------------------

# Chemin -> (taille, mtime_ns, empreinte) : la page est relue toutes les
# quelques secondes, l'empreinte n'est recalculée que si le fichier a changé.
_empreintes: Dict[str, tuple] = {}


def empreinte(path: Path) -> Optional[str]:
    """Taille + condensé du début et de la fin du fichier ; None s'il est illisible."""
    try:
        st = path.stat()
    except OSError:
        return None
    cache = _empreintes.get(str(path))
    if cache and cache[:2] == (st.st_size, st.st_mtime_ns):
        return cache[2]
    try:
        with path.open("rb") as f:
            h = hashlib.sha1(f.read(EMPREINTE_OCTETS))
            if st.st_size > EMPREINTE_OCTETS:
                f.seek(max(EMPREINTE_OCTETS, st.st_size - EMPREINTE_OCTETS))
                h.update(f.read(EMPREINTE_OCTETS))
    except OSError:
        return None
    valeur = f"{st.st_size}:{h.hexdigest()[:16]}"
    _empreintes[str(path)] = (st.st_size, st.st_mtime_ns, valeur)
    return valeur


# --- Liste pour la page ----------------------------------------------------------


def lister(messages_dir: Path, index_path: Path) -> List[dict]:
    """Une ligne par message de messages/, dans l'ordre chronologique.

    `numero` est le numéro à composer en mode restitution (§5.7) : les
    supprimés n'en ont pas, et décalent ceux qui les suivent. `a_calculer`
    signale un score absent ou périmé.
    """
    entrees = _charger_et_fusionner(index_path)
    lignes = []
    numero = 0
    for path in messages_chronologiques(messages_dir):
        e = entrees.get(path.name, {})
        statut = e.get("statut") if e.get("statut") in STATUTS else STATUT_A_TRIER
        if statut != STATUT_SUPPRIME:
            numero += 1
        valide = "empreinte" in e and e["empreinte"] == empreinte(path)
        lignes.append({
            "nom": path.name,
            "statut": statut,
            "numero": numero if statut != STATUT_SUPPRIME else None,
            "score": e.get("score") if valide else None,
            "duree_sec": e.get("duree_sec") if valide else None,
            "a_calculer": not valide,
        })
    return lignes


def chemin_message(messages_dir: Path, nom: str) -> Optional[Path]:
    """Chemin d'un message de messages/ désigné par son nom ; None s'il n'y est pas.

    Le nom vient d'une requête HTTP : il n'est accepté que s'il figure tel quel
    dans la liste des fichiers, ce qui écarte `../`, les chemins absolus et
    tout fichier hors de messages/.
    """
    for path in fichiers.fichiers_wav(messages_dir):
        if path.name == nom:
            return path
    return None


def _maintenant() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def appliquer(action: str, noms: Iterable[str], messages_dir: Path,
              index_path: Path) -> Dict[str, str]:
    """Change le statut des messages `noms` ; retourne nom -> « ok » ou « inconnu ».

    Lève ValueError pour une action inconnue. N'écrit l'index que si au moins
    un message existe.
    """
    if action not in ACTIONS:
        raise ValueError(f"action inconnue : {action}")
    statut = ACTIONS[action]
    existants = {p.name for p in fichiers.fichiers_wav(messages_dir)}
    resultats = {nom: ("ok" if nom in existants else "inconnu") for nom in noms}
    valides = [nom for nom, r in resultats.items() if r == "ok"]
    if not valides:
        return resultats
    with _verrou:
        entrees = _charger_et_fusionner(index_path)
        instant = _maintenant()
        for nom in valides:
            e = entrees.setdefault(nom, {})
            e["statut"] = statut
            e["statut_maj"] = instant
        _ecrire(index_path, entrees)
    logger.info("Tri des messages : %s -> %s (%d message(s)).", action, statut, len(valides))
    return resultats


# --- Calcul des scores -------------------------------------------------------------


def calculer_scores(messages_dir: Path, index_path: Path,
                    peut_continuer: Callable[[], bool] = lambda: True) -> int:
    """Calcule les scores absents ou périmés ; retourne le nombre de scores écrits.

    Chaque score est écrit dès qu'il est calculé : la page les voit arriver.
    `peut_continuer` est consulté avant chaque fichier (sur le Pi : le
    téléphone est-il au repos ?). Un fichier illisible reçoit un score de 0
    avec son empreinte, pour ne pas être relu à chaque passage.
    """
    ecrits = 0
    for ligne in lister(messages_dir, index_path):
        if not ligne["a_calculer"]:
            continue
        if not peut_continuer():
            logger.info("Calcul des scores interrompu : téléphone en cours d'utilisation.")
            break
        path = messages_dir / ligne["nom"]
        trace = empreinte(path)
        if trace is None:
            continue
        try:
            mesures = score_message.mesurer(path)
            note = score_message.noter(mesures)
        except score_message.ErreurScore as exc:
            logger.warning("Score de %s impossible : %s", path.name, exc)
            mesures, note = {}, 0.0
        with _verrou:
            entrees = _charger_et_fusionner(index_path)
            e = entrees.setdefault(path.name, {})
            e.update(mesures)
            e["score"] = note
            e["empreinte"] = trace
            try:
                _ecrire(index_path, entrees)
            except OSError as exc:
                logger.error("Index des messages non écrit : %s", exc)
                break
        ecrits += 1
    return ecrits


_calcul: Dict[str, Optional[threading.Thread]] = {"thread": None}


def calcul_en_cours() -> bool:
    thread = _calcul["thread"]
    return thread is not None and thread.is_alive()


def lancer_calcul_scores(messages_dir: Path, index_path: Path,
                         peut_continuer: Callable[[], bool] = lambda: True) -> bool:
    """Calcule les scores dans un thread ; un seul à la fois. True s'il a été lancé."""
    with _verrou:
        if calcul_en_cours():
            return False

        def tache() -> None:
            debut = time.monotonic()
            try:
                n = calculer_scores(messages_dir, index_path, peut_continuer)
            except Exception:  # un thread qui meurt en silence laisserait la page en attente
                logger.exception("Calcul des scores en échec.")
                return
            if n:
                logger.info("%d score(s) calculé(s) en %.1f s.", n, time.monotonic() - debut)

        _calcul["thread"] = threading.Thread(target=tache, name="scores-messages", daemon=True)
        _calcul["thread"].start()
        return True
