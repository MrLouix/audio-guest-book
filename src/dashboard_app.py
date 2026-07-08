"""Dashboard web de supervision (§5.2, §6).

Expose l'état courant (status.json), les derniers logs, le nombre de
messages enregistrés, le mode réseau + IP, et un bouton pour déclencher la
sonnerie à distance. Toutes les lectures tolèrent l'absence de fichier
(§7.4) : jamais de 500 pour une simple donnée manquante.

Toutes les routes (HTML et /api/*) sont protégées par une session Flask
authentifiée par mot de passe unique (§6) : avant chaque requête, seules
/login et les fichiers statiques sont accessibles sans session valide.
"""

import datetime
import logging
import subprocess
import time
from pathlib import Path
from typing import List

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.serving import make_server

import auth
import config
import network_info
import qr_utils
import rclone_sync
import status_io

logger = logging.getLogger(__name__)

# dashboard_app.py vit dans src/ ; templates/ et static/ restent à la
# racine du projet (§8), il faut donc les indiquer explicitement à Flask.
app = Flask(
    __name__,
    template_folder=str(config.BASE_DIR / "templates"),
    static_folder=str(config.BASE_DIR / "static"),
)
app.secret_key = auth.get_or_create_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(hours=config.SESSION_LIFETIME_HOURS)

LOG_TAIL_LINES = 150

# Seules ces routes sont accessibles sans session authentifiée (§6).
PUBLIC_PATHS = {"/login"}
PUBLIC_PATH_PREFIXES = ("/static/",)


def _is_safe_redirect_target(target: str) -> bool:
    """N'autorise qu'une redirection interne relative (protection open-redirect)."""
    return bool(target) and target.startswith("/") and not target.startswith("//")


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or request.path.startswith(PUBLIC_PATH_PREFIXES):
        return None
    if session.get("authenticated"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"erreur": "authentification requise"}), 401
    next_target = request.path
    if request.query_string:
        next_target += "?" + request.query_string.decode("utf-8", errors="ignore")
    return redirect(url_for("login", next=next_target))


@app.route("/login", methods=["GET", "POST"])
def login():
    next_target = request.values.get("next", "")
    if request.method == "POST":
        client_id = request.remote_addr or "inconnu"
        delay = auth.throttle_delay(client_id)
        if delay > 0:
            logger.warning("Connexion dashboard temporisée (%.1fs) pour %s", delay, client_id)
            time.sleep(delay)

        password = request.form.get("password", "")
        if auth.verify_password(password):
            matched_as = "user"
        elif auth.verify_admin_password(password):
            matched_as = "admin"
        else:
            matched_as = None

        if matched_as:
            auth.register_success(client_id)
            session.clear()
            session["authenticated"] = True
            session["is_admin"] = matched_as == "admin"
            session.permanent = request.form.get("remember") == "on"
            logger.info("Connexion dashboard réussie depuis %s (%s)", client_id, matched_as)
            target = next_target if _is_safe_redirect_target(next_target) else url_for("index")
            return redirect(target)

        count = auth.register_failed_attempt(client_id)
        logger.warning("Échec de connexion dashboard depuis %s (%d échec(s) consécutif(s))", client_id, count)
        return render_template("login.html", error="Mot de passe incorrect.", next=next_target), 401

    return render_template("login.html", error=None, next=next_target)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _tail_lines(path: Path, n: int) -> List[str]:
    """Les n dernières lignes d'un fichier texte ; liste vide si absent ou illisible (§7.4)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-n:]]


def _messages_count() -> int:
    if not config.MESSAGES_DIR.exists():
        return 0
    return sum(1 for p in config.MESSAGES_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".wav")


def _status_payload() -> dict:
    """Contenu combiné pour /api/status (§5.2) : état, dernière MAJ, réseau, IP, nb messages."""
    status = status_io.read_status() or {"etat": "inconnu", "derniere_maj": None, "detail": ""}
    return {
        "etat": status.get("etat", "inconnu"),
        "derniere_maj": status.get("derniere_maj"),
        "detail": status.get("detail", ""),
        "reseau": network_info.get_network_info(),
        "messages_count": _messages_count(),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify(_status_payload())


@app.route("/api/logs")
def api_logs():
    return jsonify({"lignes": _tail_lines(config.LIVRE_DOR_LOG, LOG_TAIL_LINES)})


@app.route("/api/messages/count")
def api_messages_count():
    return jsonify({"nombre": _messages_count()})


@app.route("/api/ring", methods=["POST"])
def api_ring():
    try:
        config.RING_TRIGGER_FILE.touch(exist_ok=True)
    except OSError as exc:
        logger.exception("Impossible de créer le fichier ring_trigger")
        return jsonify({"erreur": f"Impossible de créer le fichier ring_trigger : {exc}"}), 500
    return jsonify({"ok": True})


@app.route("/qr")
def qr():
    """Deux QR codes (§5.2) : URL stable du dashboard, et WiFi de l'AP si actif."""
    net = network_info.get_network_info()
    wifi_qr_data_uri = None
    if net["mode"] == "ap":
        wifi_qr_data_uri = qr_utils.qr_data_uri(qr_utils.wifi_qr_payload(config.AP_SSID, config.AP_PASSWORD))
    return render_template(
        "qr.html",
        dashboard_url=qr_utils.dashboard_url(),
        dashboard_qr_data_uri=qr_utils.qr_data_uri(qr_utils.dashboard_url()),
        wifi_qr_data_uri=wifi_qr_data_uri,
        ap_ssid=config.AP_SSID,
    )


@app.route("/qr/label")
def qr_label():
    """Étiquette imprimable de l'URL du dashboard, dimensionnée en mm (§5.2)."""
    size_mm = request.args.get("taille", default=config.QR_LABEL_SIZE_MM, type=int)
    size_mm = max(10, min(size_mm, 200))
    return render_template(
        "qr_label.html",
        size_mm=size_mm,
        qr_data_uri=qr_utils.qr_data_uri(qr_utils.dashboard_url()),
    )


@app.route("/wifi")
def wifi_page():
    """Page de provisioning WiFi par photo de QR code, décodée 100% côté client (§5.2)."""
    net = network_info.get_network_info()
    return render_template(
        "wifi.html",
        network_mode=net["mode"],
        mdns_hostname=config.MDNS_HOSTNAME,
        web_port=config.WEB_PORT,
    )


@app.route("/api/wifi/add", methods=["POST"])
def api_wifi_add():
    """Crée le profil WiFi via nmcli et tente la connexion (§5.2)."""
    data = request.get_json(silent=True) or request.form
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    if not ssid:
        return jsonify({"erreur": "SSID manquant"}), 400

    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.CONNECT_TIMEOUT_SEC)
    except FileNotFoundError:
        logger.error("nmcli introuvable sur ce système")
        return jsonify({"erreur": "nmcli introuvable sur ce système"}), 500
    except subprocess.TimeoutExpired:
        logger.error("Timeout nmcli lors de la connexion au réseau %s", ssid)
        return jsonify({"erreur": f"Délai dépassé lors de la connexion à {ssid}"}), 504

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "échec inconnu"
        logger.error("Échec de connexion WiFi à %s : %s", ssid, detail)
        return jsonify({"erreur": detail}), 502

    logger.info("Connexion WiFi réussie à %s", ssid)
    return jsonify({"ok": True, "ssid": ssid})


@app.route("/rclone", methods=["GET", "POST"])
def rclone_page():
    """Configuration de la synchronisation Google Drive (§5.4)."""
    error = None
    if request.method == "POST":
        remote = request.form.get("remote", "").strip() or config.RCLONE_REMOTE
        dossier = request.form.get("dossier", "").strip() or config.RCLONE_FOLDER
        try:
            intervalle_min = int(request.form.get("intervalle_min", config.RCLONE_INTERVAL_MIN))
        except ValueError:
            intervalle_min = config.RCLONE_INTERVAL_MIN
        intervalle_min = max(1, intervalle_min)
        actif = request.form.get("actif") == "on"

        result = rclone_sync.update_config(remote, dossier, intervalle_min, actif)
        if not result["ok"]:
            error = result["erreur"]

    return render_template("rclone.html", status=rclone_sync.get_status(), error=error)


@app.route("/api/rclone/sync-now", methods=["POST"])
def api_rclone_sync_now():
    """Lance rclone copy immédiatement et retourne le résultat (§5.2)."""
    result = rclone_sync.run_sync()
    return jsonify(result), (200 if result.get("ok") else 502)


def run_server() -> None:
    """Démarre le serveur sur WEB_PORT ; pas de repli de port (WEB_PORT_MAX_ATTEMPTS=1, §5.2).

    Le port est testé en premier via un vrai bind (make_server) : en cas
    d'échec, le service s'arrête avec un message clair plutôt que de
    basculer silencieusement sur un autre port.
    """
    config.ensure_directories()
    auth.ensure_password_configured()
    port = config.WEB_PORT
    try:
        # threaded=True : une synchronisation rclone en cours (/api/rclone/sync-now,
        # potentiellement longue) ne doit pas bloquer les autres requêtes
        # (statut, logs...) qui continuent de s'auto-rafraîchir en parallèle.
        server = make_server("0.0.0.0", port, app, threaded=True)
    except (OSError, SystemExit) as exc:
        # make_server()/Werkzeug intercepte déjà EADDRINUSE et lève un
        # SystemExit(1) en interne (selon la version, un OSError brut est
        # aussi possible) : dans les deux cas, on remplace par notre propre
        # message explicite plutôt que de laisser un code de sortie muet.
        message = (f"Port {port} indisponible (WEB_PORT_MAX_ATTEMPTS={config.WEB_PORT_MAX_ATTEMPTS}, "
                   f"pas de repli automatique) : {exc}")
        logger.error(message)
        raise SystemExit(message) from exc

    config.ACTIVE_PORT_FILE.write_text(str(port))
    logger.info("Dashboard démarré sur http://0.0.0.0:%d/", port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_server()


if __name__ == "__main__":
    main()
