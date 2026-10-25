"""Page /messages : tri des messages des invités après l'événement.

Blueprint partagé par le dashboard du Pi (dashboard_app.py) et par la page
autonome du PC (tri_messages.py). L'application qui l'enregistre fournit,
dans app.config :

- TRI_DOSSIERS : fonction sans argument -> (dossier messages, fichier index).
  Une fonction, et non deux chemins figés : les tests redirigent
  config.MESSAGES_DIR après l'import ;
- TRI_PEUT_CALCULER : fonction sans argument -> bool, consultée avant chaque
  calcul de score (sur le Pi : le téléphone est-il au repos ?) ;
- TRI_AUTONOME : True sur le PC, où le menu du dashboard n'a pas de sens.

Aucune de ces routes ne touche aux fichiers audio : seul l'index est écrit
(messages_index.py).
"""

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

import messages_index

bp = Blueprint("messages", __name__)


def _dossiers():
    return current_app.config["TRI_DOSSIERS"]()


def _peut_calculer() -> bool:
    return current_app.config.get("TRI_PEUT_CALCULER", lambda: True)()


def _liste_payload() -> dict:
    """Liste des messages ; lance le calcul des scores manquants si besoin."""
    messages_dir, index_path = _dossiers()
    lignes = messages_index.lister(messages_dir, index_path)
    if any(l["a_calculer"] for l in lignes) and _peut_calculer():
        # Le thread n'a pas de contexte Flask : la fonction est résolue ici.
        messages_index.lancer_calcul_scores(
            messages_dir, index_path, current_app.config.get("TRI_PEUT_CALCULER", lambda: True))
    return {"messages": lignes, "calcul_en_cours": messages_index.calcul_en_cours()}


@bp.route("/messages")
def page():
    return render_template("messages.html", autonome=current_app.config.get("TRI_AUTONOME", False))


@bp.route("/api/messages")
def api_liste():
    return jsonify(_liste_payload())


@bp.route("/api/messages/<nom>/audio")
def api_audio(nom: str):
    """Le fichier traité, pour le lecteur du navigateur (lecture par plages)."""
    messages_dir, _ = _dossiers()
    path = messages_index.chemin_message(messages_dir, nom)
    if path is None:
        abort(404)
    return send_file(path, mimetype="audio/wav", conditional=True)


@bp.route("/api/messages/actions", methods=["POST"])
def api_actions():
    """{action, noms: [...]} -> statut écrit dans l'index pour chaque message."""
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    noms = data.get("noms")
    if action not in messages_index.ACTIONS:
        return jsonify({"erreur": f"Action inconnue : {action!r}"}), 400
    if not isinstance(noms, list) or not noms or not all(isinstance(n, str) for n in noms):
        return jsonify({"erreur": "Aucun message sélectionné."}), 400
    messages_dir, index_path = _dossiers()
    try:
        resultats = messages_index.appliquer(action, noms, messages_dir, index_path)
    except OSError as exc:
        return jsonify({"erreur": f"Index des messages non enregistré : {exc}"}), 500
    payload = _liste_payload()
    payload["resultats"] = resultats
    return jsonify(payload)
