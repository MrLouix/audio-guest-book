"""Association « rôle sonore -> fichier source », éditable depuis le dashboard (§4.2).

Chaque son attendu par la machine à états est un *rôle* : `sonnerie`,
`message_generique`, `message_0` … `message_9`, `aucun_message`. Ce module dit
quel fichier de `audio_src/` alimente chaque rôle, pour que les fichiers
déposés depuis un smartphone gardent leur nom d'origine
(« sonnerie_cloches.mp3 ») au lieu de devoir être renommés à la main.

Le choix est sérialisé dans `audio_config.json` (contrat du §8) :

    { "roles": { "sonnerie": "cloches.mp3", "message_3": "papy.m4a" } }

Fichier dédié, et non `custom_config.json` : ce dernier n'est lu qu'une fois,
à l'import de `config.py`, et impose un redémarrage du service, alors que le
mapping doit être relu à chaque conversion.

**Repli** : un rôle sans entrée explicite retombe sur l'ancienne convention de
nommage `audio_src/<role>.*`. Une installation antérieure continue donc de
fonctionner sans `audio_config.json`.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# Extensions acceptées en entrée : ce que ffmpeg sait décoder, et ce que
# produisent les dictaphones Android et iOS.
SOURCE_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
    ".flac", ".aiff", ".aif", ".wma", ".3gp", ".amr",
}

DEFAULT_CONFIG = {"roles": {}}

ROLE_SONNERIE = "sonnerie"
ROLE_MESSAGE_GENERIQUE = "message_generique"
ROLE_AUCUN_MESSAGE = "aucun_message"

# Ordre d'affichage dans le dashboard.
ROLE_NAMES: List[str] = (
    [ROLE_SONNERIE, ROLE_MESSAGE_GENERIQUE]
    + [f"message_{digit}" for digit in range(10)]
    + [ROLE_AUCUN_MESSAGE]
)

ROLE_LABELS: Dict[str, str] = {
    ROLE_SONNERIE: "Sonnerie",
    ROLE_MESSAGE_GENERIQUE: "Message générique (repli)",
    ROLE_AUCUN_MESSAGE: "Annonce « aucun message » (mode restitution)",
    **{f"message_{d}": f"Message du chiffre {d}" for d in range(10)},
}

# Rôles sans lesquels livre_dor.py refuse de démarrer (§7.2).
REQUIRED_ROLES = {ROLE_MESSAGE_GENERIQUE}


def target_for(role: str) -> Optional[Path]:
    """Fichier WAV généré pour ce rôle, dans audio/.

    Résolu à l'appel et non à l'import : tests/harness.py réassigne les
    chemins de config.py à l'entrée de chaque scénario.
    """
    if role == ROLE_SONNERIE:
        return config.RING_OUT_WAV
    if role == ROLE_MESSAGE_GENERIQUE:
        return config.MESSAGE_GENERIQUE_WAV
    if role == ROLE_AUCUN_MESSAGE:
        return config.AUCUN_MESSAGE_WAV
    if role.startswith("message_"):
        suffixe = role[len("message_"):]
        if suffixe.isdigit() and len(suffixe) == 1:
            return config.message_wav(int(suffixe))
    return None


# --- Lecture / écriture de audio_config.json ----------------------------


def read_config() -> dict:
    """Mapping courant ; valeurs par défaut si le fichier est absent ou illisible (§7.4)."""
    try:
        data = json.loads(config.AUDIO_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"roles": {}}
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        return {"roles": {}}
    # On ne garde que les rôles connus et les valeurs textuelles : le fichier
    # peut avoir été édité à la main.
    return {"roles": {nom: valeur for nom, valeur in roles.items()
                      if nom in ROLE_NAMES and isinstance(valeur, str) and valeur}}


def write_config(cfg: dict) -> None:
    """Écriture atomique (fichier temporaire + os.replace, cohérent avec status_io.py)."""
    config.AUDIO_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.AUDIO_CONFIG_FILE.with_suffix(config.AUDIO_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, config.AUDIO_CONFIG_FILE)


def ensure_config_exists() -> None:
    if not config.AUDIO_CONFIG_FILE.exists():
        write_config(dict(DEFAULT_CONFIG))


# --- Sources disponibles -------------------------------------------------


def available_sources() -> List[str]:
    """Noms des fichiers audio présents dans audio_src/, triés, sans les cachés."""
    try:
        entries = list(config.AUDIO_SRC_DIR.iterdir())
    except OSError:
        return []
    return sorted(
        entry.name for entry in entries
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() in SOURCE_EXTENSIONS
    )


def _is_safe_name(nom: str) -> bool:
    """Le nom désigne-t-il bien un fichier *dans* audio_src/ et rien d'autre ?

    Le nom vient d'une requête HTTP : `../../etc/passwd` ou un chemin absolu
    ne doivent jamais arriver jusqu'à un open().
    """
    return bool(nom) and Path(nom).name == nom and nom not in (".", "..")


def source_for(role: str) -> Optional[Path]:
    """Fichier source de ce rôle : mapping explicite, puis repli sur la convention.

    Précédence :
    1. le fichier choisi dans le dashboard, s'il existe toujours ;
    2. l'ancienne convention `audio_src/<role>.*` (premier par ordre alphabétique) ;
    3. None — le rôle n'a pas de source.
    """
    nom = read_config()["roles"].get(role)
    if nom and _is_safe_name(nom):
        chemin = config.AUDIO_SRC_DIR / nom
        if chemin.is_file():
            return chemin
        logger.warning("Source %r du rôle %r introuvable : repli sur la convention "
                       "de nommage.", nom, role)

    candidats = sorted(p for p in config.AUDIO_SRC_DIR.glob(f"{role}.*")
                       if p.is_file() and p.suffix.lower() in SOURCE_EXTENSIONS)
    return candidats[0] if candidats else None


def origin_for(role: str) -> str:
    """D'où vient la source du rôle : "mapping", "convention" ou "absent"."""
    nom = read_config()["roles"].get(role)
    if nom and _is_safe_name(nom) and (config.AUDIO_SRC_DIR / nom).is_file():
        return "mapping"
    return "convention" if source_for(role) is not None else "absent"


def set_role_sources(mapping: Dict[str, Optional[str]]) -> dict:
    """Enregistre le choix de fichier de plusieurs rôles.

    Une valeur vide ou None efface l'entrée du rôle (retour à la convention de
    nommage). Retourne {"ok", "erreur", "roles_modifies"} ; rien n'est écrit
    si une seule valeur est invalide.
    """
    cfg = read_config()
    roles = dict(cfg["roles"])
    modifies = []
    sources = set(available_sources())

    for role, nom in mapping.items():
        if role not in ROLE_NAMES:
            return {"ok": False, "erreur": f"Rôle inconnu : {role}", "roles_modifies": []}

        if not nom:
            if roles.pop(role, None) is not None:
                modifies.append(role)
            continue

        nom = str(nom)
        if not _is_safe_name(nom):
            return {"ok": False, "erreur": f"Nom de fichier invalide pour {role} : {nom}",
                    "roles_modifies": []}
        if nom not in sources:
            return {"ok": False,
                    "erreur": f"Fichier absent de {config.AUDIO_SRC_DIR.name}/ pour "
                              f"{ROLE_LABELS.get(role, role)} : {nom}",
                    "roles_modifies": []}
        if roles.get(role) != nom:
            roles[role] = nom
            modifies.append(role)

    try:
        write_config({"roles": roles})
    except OSError as exc:
        return {"ok": False, "erreur": f"Impossible d'écrire audio_config.json : {exc}",
                "roles_modifies": []}

    return {"ok": True, "erreur": None, "roles_modifies": modifies}


def mapping_status() -> List[dict]:
    """État de chaque rôle, pour la page /settings du dashboard (§5.2)."""
    cfg = read_config()["roles"]
    etat = []
    for role in ROLE_NAMES:
        source = source_for(role)
        cible = target_for(role)
        cible_existe = bool(cible and cible.exists())

        # « Périmée » : la source a changé depuis la dernière conversion —
        # typiquement après une synchronisation Drive. C'est ce qui déclenche
        # le badge invitant à reconvertir.
        perimee = False
        if source is not None and cible_existe:
            try:
                perimee = source.stat().st_mtime > cible.stat().st_mtime
            except OSError:
                perimee = False

        etat.append({
            "nom": role,
            "label": ROLE_LABELS.get(role, role),
            "source": cfg.get(role),
            "source_effective": source.name if source else None,
            "origine": origin_for(role),
            "cible": cible.name if cible else None,
            "pret": cible_existe,
            "perimee": perimee,
            "obligatoire": role in REQUIRED_ROLES,
        })
    return etat
