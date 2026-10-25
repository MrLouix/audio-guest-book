"""Page /messages en autonome sur un PC : trier les messages hors du Pi.

Sur le PC, Google Drive pour ordinateur synchronise les deux dossiers du Drive
(§5.4) : celui des messages (copie montante du Pi) et celui d'audio_src/
(bidirectionnel). Ce script ouvre, sur ces dossiers, la même page que le
dashboard du Pi. Les choix (conserver, supprimer, restaurer) et les scores
sont écrits dans l'index d'audio_src/, qui redescend sur le Pi à la synchro
suivante ; aucun fichier audio n'est jamais modifié.

Les dossiers sont cherchés dans `racine` (par défaut le dossier courant)
sous leur nom sur le Pi (messages, audio_src) ou sur le Drive
(RCLONE_FOLDER, RCLONE_SOURCES_FOLDER) ; --messages et --audio-src les
désignent directement.

Prérequis sur le PC : Python 3, et `pip install flask numpy`.

Pas d'authentification : le serveur n'écoute que sur cette machine
(127.0.0.1).

Usage :
    python src/tri_messages.py                         # dossier courant
    python src/tri_messages.py "G:\\Mon Drive"
    python src/tri_messages.py --messages D:\\msg --audio-src D:\\sources
"""

import argparse
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Iterable, Optional

from flask import Flask, redirect, url_for
from werkzeug.serving import make_server

import config
import fichiers
import messages_page

PORT_PAR_DEFAUT = 5050

logger = logging.getLogger(__name__)


def trouver_dossier(racine: Path, noms: Iterable[str]) -> Optional[Path]:
    """Premier sous-dossier de `racine` portant l'un des `noms` ; None sinon."""
    for nom in noms:
        candidat = racine / nom
        if candidat.is_dir():
            return candidat
    return None


def creer_app(messages_dir: Path, audio_src_dir: Path) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )
    index_path = audio_src_dir / config.MESSAGES_INDEX_NAME
    app.config.update(
        TRI_DOSSIERS=lambda: (messages_dir, index_path),
        TRI_PEUT_CALCULER=lambda: True,
        TRI_AUTONOME=True,
    )
    app.register_blueprint(messages_page.bp)

    @app.route("/")
    def accueil():
        return redirect(url_for("messages.page"))

    return app


def _dossiers(args: argparse.Namespace) -> tuple:
    racine = Path(args.racine).expanduser()
    messages_dir = (Path(args.messages).expanduser() if args.messages
                    else trouver_dossier(racine, ("messages", config.RCLONE_FOLDER)))
    audio_src_dir = (Path(args.audio_src).expanduser() if args.audio_src
                     else trouver_dossier(racine, ("audio_src", config.RCLONE_SOURCES_FOLDER)))
    if messages_dir is None or not messages_dir.is_dir():
        raise SystemExit(f"Dossier des messages introuvable dans {racine} "
                         f"(messages ou {config.RCLONE_FOLDER}) : précisez --messages.")
    if audio_src_dir is None or not audio_src_dir.is_dir():
        raise SystemExit(f"Dossier audio_src introuvable dans {racine} "
                         f"(audio_src ou {config.RCLONE_SOURCES_FOLDER}) : précisez --audio-src. "
                         "L'index doit y être écrit pour remonter au Pi.")
    return messages_dir, audio_src_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("racine", nargs="?", default=".",
                        help="dossier contenant les deux dossiers synchronisés (défaut : courant)")
    parser.add_argument("--messages", help="dossier des messages, s'il est ailleurs")
    parser.add_argument("--audio-src", help="dossier audio_src, s'il est ailleurs")
    parser.add_argument("--port", type=int, default=PORT_PAR_DEFAUT)
    parser.add_argument("--sans-navigateur", action="store_true",
                        help="ne pas ouvrir la page dans le navigateur")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=fichiers.FORMAT_JOURNAL)
    messages_dir, audio_src_dir = _dossiers(args)
    app = creer_app(messages_dir, audio_src_dir)
    try:
        server = make_server("127.0.0.1", args.port, app, threaded=True)
    except (OSError, SystemExit) as exc:
        raise SystemExit(f"Port {args.port} indisponible : {exc} (essayez --port).") from exc

    adresse = f"http://127.0.0.1:{args.port}/messages"
    print(f"Messages : {messages_dir}\nIndex    : {audio_src_dir / config.MESSAGES_INDEX_NAME}")
    print(f"Page de tri : {adresse}  (Ctrl+C pour arrêter)")
    if not args.sans_navigateur:
        threading.Timer(1.0, webbrowser.open, args=(adresse,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
