#!/usr/bin/env python3
"""Test unitaire du TRI DES MESSAGES : score, index et page /messages.

Après l'événement, le client trie les messages des invités sur la page
/messages, depuis le dashboard du Pi ou depuis son PC (tri_messages.py).
Aucun fichier audio n'est jamais touché : statut et score vivent dans
audio_src/index_messages.json, que la restitution consulte pour sauter les
messages supprimés.

Ce que vérifie ce script, seul et sans matériel :

1. la formule du score (durée, RSB, part de parole, message long) ;
2. le score de vrais WAV synthétiques ;
3. l'index : statuts, numérotation de restitution, actions, noms refusés,
   et aucun fichier audio modifié ;
4. la fusion des copies laissées par un conflit de synchro ;
5. l'empreinte : score gardé tant que le fichier ne change pas ;
6. la page autonome du PC et la page du dashboard (Flask, sans serveur) ;
7. tri_messages.pyz : l'archive lancée seule, hors du dépôt.

Usage :
    python3 tests/test_messages.py
"""

import json
import tempfile
import time
from pathlib import Path

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Banc, Rapport

import config                        # noqa: E402
import messages_index                # noqa: E402
import score_message                 # noqa: E402
import traitement_audio              # noqa: E402

np = traitement_audio.np
SR = 16000


def ecrire_message(chemin: Path, secondes: float, voix: float = 0.3, bruit: float = 0.001,
                   part_voix: float = 0.65, graine: int = 1) -> Path:
    """WAV stéréo 16 kHz : salves de « voix » (sinusoïde modulée) sur un bruit de fond.

    `part_voix` est la part de chaque période de 1,5 s occupée par la voix.
    """
    rng = np.random.default_rng(graine)
    t = np.arange(int(secondes * SR)) / SR
    salves = ((t % 1.5) < 1.5 * part_voix).astype(float)
    d = voix * salves * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    d = d + bruit * rng.standard_normal(len(t))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    traitement_audio.ecrire(chemin, d, SR)
    return chemin


def test_formule(rapport: Rapport) -> None:
    rapport.section("1. Formule du score")
    noter = score_message.noter
    rapport.egal("moins de 5 s : 0, même avec un RSB parfait",
                 noter({"duree_sec": 4.9, "rsb_db": 40, "part_parole": 0.8}), 0.0)
    rapport.egal("RSB au plafond, assez de parole : 10",
                 noter({"duree_sec": 20, "rsb_db": 30, "part_parole": 0.6}), 10.0)
    rapport.egal("RSB à mi-chemin (18 dB) : 5",
                 noter({"duree_sec": 20, "rsb_db": 18, "part_parole": 0.6}), 5.0)
    rapport.egal("RSB sous le plancher : 0",
                 noter({"duree_sec": 20, "rsb_db": 3, "part_parole": 0.6}), 0.0)
    rapport.egal("moitié moins de parole que le seuil : note divisée par deux",
                 noter({"duree_sec": 20, "rsb_db": 30, "part_parole": 0.15}), 5.0)
    rapport.egal("plus de 3 min : note divisée par deux",
                 noter({"duree_sec": 200, "rsb_db": 30, "part_parole": 0.6}), 5.0)


def test_wav(rapport: Rapport, dossier: Path) -> None:
    rapport.section("2. Score de WAV synthétiques")
    cas = {
        "net": ecrire_message(dossier / "net.wav", 20),
        "bruité": ecrire_message(dossier / "bruite.wav", 20, bruit=0.08),
        "court": ecrire_message(dossier / "court.wav", 3),
        "long": ecrire_message(dossier / "long.wav", 240),
        "blanc": ecrire_message(dossier / "blanc.wav", 60, part_voix=0.08),
    }
    notes = {nom: score_message.noter(score_message.mesurer(p)) for nom, p in cas.items()}
    rapport.info("notes : " + ", ".join(f"{n} {v}" for n, v in notes.items()))
    rapport.verifie("message net de 20 s : note haute (≥ 8)", notes["net"] >= 8, notes)
    rapport.verifie("même message noyé dans le bruit : note basse (≤ 3)", notes["bruité"] <= 3, notes)
    rapport.egal("message de 3 s : 0", notes["court"], 0.0)
    rapport.verifie("message de 4 min : pénalisé (≤ moitié du message net)",
                    notes["long"] <= notes["net"] / 2 + 0.1, notes)
    rapport.verifie("message presque vide : pénalisé (≤ 4)", notes["blanc"] <= 4, notes)

    illisible = dossier / "illisible.wav"
    illisible.write_bytes(b"pas un wav")
    try:
        score_message.mesurer(illisible)
        rapport.verifie("un fichier illisible lève ErreurScore", False)
    except score_message.ErreurScore:
        rapport.verifie("un fichier illisible lève ErreurScore", True)


def _empreintes_audio(dossier: Path) -> dict:
    return {p.name: (p.stat().st_size, p.read_bytes()) for p in dossier.rglob("*.wav")}


def test_index(rapport: Rapport, dossier: Path) -> None:
    rapport.section("3. Index : statuts, numérotation, actions")
    messages = dossier / "messages"
    index = dossier / "audio_src" / config.MESSAGES_INDEX_NAME
    noms = [f"message_2026-06-20_14-0{i}-00.wav" for i in range(4)]
    for i, nom in enumerate(noms):
        ecrire_message(messages / nom, 6, graine=i)
    (messages / "brut").mkdir()
    ecrire_message(messages / "brut" / noms[0], 6)
    (messages / noms[0]).with_suffix(".txt").write_text("bonjour", encoding="utf-8")
    avant = _empreintes_audio(messages)

    rapport.egal("index absent : aucun supprimé", messages_index.noms_supprimes(index), set())
    lignes = messages_index.lister(messages, index)
    rapport.egal("une ligne par message, dans l'ordre chronologique",
                 [l["nom"] for l in lignes], noms)
    rapport.egal("index absent : tout est à trier",
                 {l["statut"] for l in lignes}, {messages_index.STATUT_A_TRIER})
    rapport.egal("le brut de messages/brut/ n'est pas listé", len(lignes), 4)
    rapport.verifie("lister n'écrit pas d'index tant que rien n'est décidé", not index.exists())

    r = messages_index.appliquer("conserver", [noms[0], "inconnu.wav", "../secret.wav"],
                                 messages, index)
    rapport.egal("les noms absents de messages/ sont refusés",
                 r, {noms[0]: "ok", "inconnu.wav": "inconnu", "../secret.wav": "inconnu"})
    messages_index.appliquer("supprimer", [noms[1], noms[2]], messages, index)
    lignes = {l["nom"]: l for l in messages_index.lister(messages, index)}
    rapport.egal("statuts écrits", [lignes[n]["statut"] for n in noms],
                 ["conserve", "supprime", "supprime", "a_trier"])
    rapport.egal("numéros de restitution : les supprimés n'en ont pas et décalent les suivants",
                 [lignes[n]["numero"] for n in noms], [1, None, None, 2])
    rapport.egal("noms_supprimes() pour la restitution",
                 messages_index.noms_supprimes(index), {noms[1], noms[2]})

    messages_index.appliquer("restaurer", [noms[1]], messages, index)
    messages_index.appliquer("ne_plus_conserver", [noms[0]], messages, index)
    lignes = {l["nom"]: l for l in messages_index.lister(messages, index)}
    rapport.egal("restaurer et ne plus conserver : retour à « à trier »",
                 [lignes[n]["statut"] for n in noms], ["a_trier", "a_trier", "supprime", "a_trier"])
    rapport.egal("aucun fichier audio créé, modifié ni supprimé",
                 _empreintes_audio(messages), avant)
    rapport.verifie("la transcription est intacte",
                    (messages / noms[0]).with_suffix(".txt").read_text(encoding="utf-8") == "bonjour")
    try:
        messages_index.appliquer("effacer", [noms[0]], messages, index)
        rapport.verifie("une action inconnue lève ValueError", False)
    except ValueError:
        rapport.verifie("une action inconnue lève ValueError", True)
    rapport.egal("chemin_message refuse ../", messages_index.chemin_message(messages, "../x.wav"), None)

    index.write_text("{ cassé", encoding="utf-8")
    rapport.egal("index illisible : tout redevient à trier, sans exception",
                 {l["statut"] for l in messages_index.lister(messages, index)}, {"a_trier"})


def test_conflits(rapport: Rapport, dossier: Path) -> None:
    rapport.section("4. Fusion des copies en conflit de synchro")
    messages = dossier / "messages"
    index = dossier / "audio_src" / config.MESSAGES_INDEX_NAME
    a, b, c = (f"message_2026-06-21_10-0{i}-00.wav" for i in range(3))
    for nom in (a, b, c):
        ecrire_message(messages / nom, 6)
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"version": 1, "messages": {
        a: {"statut": "conserve", "statut_maj": "2026-10-25T10:00:00Z", "score": 6.0},
        b: {"statut": "conserve", "statut_maj": "2026-10-25T12:00:00Z"},
    }}), encoding="utf-8")
    # Copie du PC (Google Drive pour ordinateur) et copie de rclone bisync.
    (index.parent / "index_messages (1).json").write_text(json.dumps({"version": 1, "messages": {
        a: {"statut": "supprime", "statut_maj": "2026-10-25T11:00:00Z", "score": 9.0},
        b: {"statut": "supprime", "statut_maj": "2026-10-25T09:00:00Z"},
    }}), encoding="utf-8")
    (index.parent / "index_messages.json.path2").write_text(json.dumps({"version": 1, "messages": {
        c: {"statut": "conserve", "statut_maj": "2026-10-25T08:00:00Z"},
    }}), encoding="utf-8")
    autre = index.parent / "index_messages_notes.txt"
    autre.write_text("notes à la main", encoding="utf-8")

    lignes = {l["nom"]: l["statut"] for l in messages_index.lister(messages, index)}
    rapport.egal("le statut modifié le plus récemment gagne, message par message",
                 lignes, {a: "supprime", b: "conserve", c: "conserve"})
    rapport.egal("le score de l'index principal est gardé",
                 messages_index.lire(index)[a].get("score"), 6.0)
    rapport.egal("les copies fusionnées sont supprimées",
                 sorted(p.name for p in messages_index.copies_en_conflit(index)),
                 ["index_messages_notes.txt"])
    rapport.verifie("un fichier qui n'est pas un index n'est pas touché", autre.exists())


def test_scores_index(rapport: Rapport, dossier: Path) -> None:
    rapport.section("5. Scores dans l'index et empreinte")
    messages = dossier / "messages"
    index = dossier / "audio_src" / config.MESSAGES_INDEX_NAME
    net = ecrire_message(messages / "message_2026-06-22_09-00-00.wav", 20)
    court = ecrire_message(messages / "message_2026-06-22_09-01-00.wav", 3)

    rapport.verifie("scores à calculer au départ",
                    all(l["a_calculer"] for l in messages_index.lister(messages, index)))
    rapport.egal("calculer_scores écrit un score par message",
                 messages_index.calculer_scores(messages, index), 2)
    entrees = messages_index.lire(index)
    rapport.verifie("score, mesures et empreinte enregistrés dans l'index",
                    all(k in entrees[net.name] for k in messages_index.CHAMPS_SCORE), entrees)
    rapport.egal("message trop court : 0 dans l'index", entrees[court.name]["score"], 0.0)
    rapport.egal("rien à recalculer au second passage",
                 messages_index.calculer_scores(messages, index), 0)

    ecrire_message(net, 20, bruit=0.08)       # retraité : même taille, autre contenu
    lignes = {l["nom"]: l for l in messages_index.lister(messages, index)}
    rapport.verifie("fichier modifié : score périmé, à recalculer",
                    lignes[net.name]["a_calculer"] and lignes[net.name]["score"] is None)
    rapport.egal("le téléphone en cours d'utilisation interrompt le calcul",
                 messages_index.calculer_scores(messages, index, lambda: False), 0)
    messages_index.appliquer("conserver", [net.name], messages, index)
    messages_index.calculer_scores(messages, index)
    e = messages_index.lire(index)[net.name]
    rapport.verifie("nouveau score plus bas, statut conservé",
                    e["score"] < entrees[net.name]["score"] and e["statut"] == "conserve", e)


def _attendre_calcul() -> None:
    limite = time.monotonic() + 30
    while messages_index.calcul_en_cours() and time.monotonic() < limite:
        time.sleep(0.05)


def test_pages(rapport: Rapport, dossier: Path) -> None:
    rapport.section("6. Pages Flask : autonome (PC) et dashboard (Pi)")
    try:
        import tri_messages           # noqa: E402  (Flask n'est requis qu'ici)
    except ImportError as exc:
        rapport.ignore("pages Flask", f"{exc.name} absent")
        return

    racine = dossier / "drive"
    messages = racine / config.RCLONE_FOLDER
    sources = racine / config.RCLONE_SOURCES_FOLDER
    sources.mkdir(parents=True)
    nom = "message_2026-06-23_18-00-00.wav"
    ecrire_message(messages / nom, 8)
    rapport.egal("dossiers trouvés sous leur nom Drive",
                 (tri_messages.trouver_dossier(racine, ("messages", config.RCLONE_FOLDER)),
                  tri_messages.trouver_dossier(racine, ("audio_src", config.RCLONE_SOURCES_FOLDER))),
                 (messages, sources))

    client = tri_messages.creer_app(messages, sources).test_client()
    page = client.get("/messages")
    rapport.verifie("page autonome servie, sans le menu du dashboard",
                    page.status_code == 200 and "Tri des messages" in page.get_data(as_text=True)
                    and "Déconnexion" not in page.get_data(as_text=True))
    rapport.egal("/ redirige vers /messages", client.get("/").status_code, 302)
    liste = client.get("/api/messages").get_json()
    rapport.egal("/api/messages liste le message", [l["nom"] for l in liste["messages"]], [nom])
    _attendre_calcul()
    liste = client.get("/api/messages").get_json()
    rapport.verifie("le score est calculé en arrière-plan", liste["messages"][0]["score"] is not None,
                    liste)
    r = client.post("/api/messages/actions", json={"action": "supprimer", "noms": [nom]})
    rapport.verifie("action groupée : statut renvoyé avec la liste",
                    r.status_code == 200 and r.get_json()["messages"][0]["statut"] == "supprime")
    rapport.verifie("l'index est écrit dans le dossier audio_src synchronisé",
                    nom in messages_index.noms_supprimes(sources / config.MESSAGES_INDEX_NAME))
    rapport.egal("action inconnue : 400",
                 client.post("/api/messages/actions", json={"action": "x", "noms": [nom]}).status_code, 400)
    rapport.egal("sélection vide : 400",
                 client.post("/api/messages/actions", json={"action": "conserver", "noms": []}).status_code,
                 400)
    audio = client.get(f"/api/messages/{nom}/audio", headers={"Range": "bytes=0-99"})
    rapport.verifie("lecture : WAV servi par plages (avance rapide du lecteur)",
                    audio.status_code == 206 and audio.mimetype == "audio/wav"
                    and len(audio.data) == 100)
    audio.close()
    rapport.egal("lecture d'un nom inconnu : 404",
                 client.get("/api/messages/inconnu.wav/audio").status_code, 404)

    # La clé de session est lue (ou créée) à l'import du dashboard : jamais
    # dans le secret_key.txt du dépôt.
    config.SECRET_KEY_FILE = dossier / "secret_key.txt"
    import dashboard_app              # noqa: E402
    with Banc(machine=False):
        ecrire_message(config.MESSAGES_DIR / nom, 8)
        client = dashboard_app.app.test_client()
        rapport.egal("dashboard : /api/messages exige une session",
                     client.get("/api/messages").status_code, 401)
        with client.session_transaction() as session:
            session["authenticated"] = True   # utilisateur ordinaire, pas admin
        r = client.post("/api/messages/actions", json={"action": "supprimer", "noms": [nom]})
        rapport.egal("dashboard : un utilisateur ordinaire peut trier", r.status_code, 200)
        rapport.verifie("dashboard : index écrit dans audio_src/ du Pi",
                        config.MESSAGES_INDEX_FILE.parent == config.AUDIO_SRC_DIR
                        and nom in messages_index.noms_supprimes(config.MESSAGES_INDEX_FILE))
        page = client.get("/messages").get_data(as_text=True)
        rapport.verifie("dashboard : page avec le menu", "Déconnexion" in page)
        r = client.get("/messages/tri_messages.pyz")
        rapport.verifie("dashboard : tri_messages.pyz en téléchargement",
                        r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
                        and b"__main__.py" in r.data)
        _attendre_calcul()


def _get(url: str, essais: int = 100):
    """GET sur le serveur lancé à part ; réessaie le temps qu'il démarre."""
    import urllib.request
    for _ in range(essais):
        try:
            with urllib.request.urlopen(url, timeout=2) as reponse:
                return reponse.status, reponse.read()
        except OSError:
            time.sleep(0.1)
    return None, b""


def test_archive_pc(rapport: Rapport, dossier: Path) -> None:
    rapport.section("7. tri_messages.pyz : la page de tri sur PC, sans le dépôt")
    import socket
    import subprocess
    import sys
    import zipfile
    import paquet_pc                  # noqa: E402

    # Dossier du PC : l'archive posée à côté des deux dossiers synchronisés,
    # loin du dépôt — rien ne peut être importé depuis src/.
    racine = dossier / "Mon Drive"
    messages = racine / config.RCLONE_FOLDER
    sources = racine / config.RCLONE_SOURCES_FOLDER
    sources.mkdir(parents=True)
    nom = "message_2026-06-24_12-00-00.wav"
    ecrire_message(messages / nom, 8)
    archive = racine / paquet_pc.NOM
    paquet_pc.construire(archive)
    with zipfile.ZipFile(archive) as zf:
        contenu = set(zf.namelist())
    rapport.verifie("l'archive ne contient que la page de tri",
                    contenu == {"__main__.py", *paquet_pc.MODULES, *paquet_pc.RESSOURCES}, contenu)
    rapport.info(f"taille : {archive.stat().st_size // 1024} Ko")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Lancée comme d'un double-clic : sans argument, depuis son propre dossier.
    serveur = subprocess.Popen([sys.executable, str(archive), "--sans-navigateur", "--port", str(port)],
                               cwd=racine, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        statut, page = _get(base + "/messages")
        rapport.verifie("page servie depuis l'archive",
                        statut == 200 and "Tri des messages" in page.decode("utf-8"),
                        f"statut : {statut}")
        statut, js = _get(base + "/static/messages.js")
        rapport.verifie("JS servi depuis l'archive", statut == 200 and b"/api/messages" in js)
        statut, _ = _get(base + "/static/dashboard.css")
        rapport.egal("CSS servi depuis l'archive", statut, 200)
        statut, data = _get(base + "/api/messages")
        rapport.verifie("dossiers Drive trouvés à côté de l'archive",
                        statut == 200 and json.loads(data)["messages"][0]["nom"] == nom)
        score = None
        for _ in range(100):
            score = json.loads(_get(base + "/api/messages")[1])["messages"][0]["score"]
            if score is not None:
                break
            time.sleep(0.1)
        rapport.verifie("score calculé par le PC depuis l'archive", score is not None)
        rapport.verifie("et écrit dans l'index du dossier audio_src synchronisé",
                        "score" in messages_index.lire(sources / config.MESSAGES_INDEX_NAME).get(nom, {}))
    finally:
        serveur.terminate()
        sortie = serveur.communicate(timeout=10)[0].decode("utf-8", "replace")
    if rapport.echecs:
        rapport.info(sortie)


def main() -> None:
    harness.parseur(__doc__, reel=False).parse_args()
    rapport = Rapport("TRI DES MESSAGES", "score, index et page /messages")
    if np is None:
        rapport.ignore("toutes les vérifications", "numpy absent")
        rapport.conclure()
    test_formule(rapport)
    for test in (test_wav, test_index, test_conflits, test_scores_index, test_pages,
                 test_archive_pc):
        with tempfile.TemporaryDirectory(prefix="tri_messages_") as tmp:
            test(rapport, Path(tmp))
    rapport.conclure()


if __name__ == "__main__":
    main()
