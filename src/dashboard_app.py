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
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.serving import make_server

import alsa_io
import audio_config
import audio_io
import auth
import config
import gpio_io
import mode_io
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

# Taille de la fenêtre lue en fin de fichier pour extraire LOG_TAIL_LINES.
# 150 lignes de log applicatif pèsent ~15 Ko ; 64 Ko laissent une marge
# confortable même pour des lignes longues (traces d'exception).
LOG_TAIL_BYTES = 64 * 1024

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
    """Les n dernières lignes d'un fichier texte ; liste vide si absent ou illisible (§7.4).

    Seule la fin du fichier est lue (LOG_TAIL_BYTES), jamais le fichier entier :
    le dashboard interroge /api/logs toutes les 8 secondes et livre_dor.log monte
    jusqu'à 1 Mo avant rotation, ce qui représentait autant de travail inutile à
    chaque rafraîchissement sur un Pi Zero.
    """
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - LOG_TAIL_BYTES)
            f.seek(start)
            raw = f.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    # Lecture démarrée en plein fichier : la première ligne est presque
    # toujours coupée en son milieu, on l'écarte.
    if start > 0 and lines:
        lines = lines[1:]
    return lines[-n:]


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
        "mode_restitution": mode_io.is_restitution(),
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
        sources_dossier = (request.form.get("sources_dossier", "").strip()
                           or config.RCLONE_SOURCES_FOLDER)
        sources_actif = request.form.get("sources_actif") == "on"

        result = rclone_sync.update_config(remote, dossier, intervalle_min, actif,
                                           sources_dossier=sources_dossier,
                                           sources_actif=sources_actif)
        if not result["ok"]:
            error = result["erreur"]

    return render_template("rclone.html", status=rclone_sync.get_status(), error=error)


@app.route("/mode", methods=["GET", "POST"])
def mode_page():
    """Bascule entre mode mariage et mode restitution (§5.7).

    La bascule est prise en compte à chaud par livre_dor.py, au retour en état
    attente : jamais au milieu d'une communication en cours.
    """
    error = None
    if request.method == "POST":
        restitution = request.form.get("restitution") == "on"
        try:
            mode_io.write_mode(restitution)
        except OSError as exc:
            logger.exception("Impossible d'écrire %s", config.MODE_CONFIG_FILE)
            error = f"Impossible d'enregistrer le mode : {exc}"
        else:
            logger.info("Mode %s activé depuis le dashboard",
                        "restitution" if restitution else "mariage")

    return render_template("mode.html",
                           restitution=mode_io.is_restitution(),
                           messages_count=_messages_count(),
                           digits_max=config.RESTITUTION_DIGITS_MAX,
                           interdigit_sec=config.RESTITUTION_INTERDIGIT_SEC,
                           error=error)


@app.route("/api/rclone/sync-now", methods=["POST"])
def api_rclone_sync_now():
    """Lance un cycle complet immédiatement et retourne le résultat (§5.2)."""
    result = rclone_sync.run_sync()
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/rclone/resync", methods=["POST"])
def api_rclone_resync():
    """Réinitialise la synchronisation bidirectionnelle de audio_src/ (§5.4).

    Réservé à l'administrateur : ce premier passage établit l'état de
    référence et peut transférer beaucoup — à faire à l'installation, pas
    pendant l'événement.
    """
    if not session.get("is_admin"):
        return jsonify({"erreur": "Seul un administrateur peut réinitialiser la synchronisation"}), 403
    result = rclone_sync.run_bisync_sources(resync=True)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/settings")
def settings_page():
    """Page de configuration des paramètres du système (§5.2)."""
    # Récupérer les valeurs actuelles des paramètres
    current_config = config.get_all_config()
    
    # Récupérer le statut GPIO
    gpio_status = gpio_io.get_current_status()
    
    # Vérifier si la carte son est disponible
    sound_card_available = audio_io.sound_card_available()
    
    return render_template(
        "settings.html",
        config=current_config,
        modifiable_params=config.MODIFIABLE_PARAMS,
        gpio_status=gpio_status,
        gpio_available=gpio_io.is_gpio_available(),
        sound_card=config.SOUND_CARD,
        sound_card_available=sound_card_available,
        audio_roles=audio_config.mapping_status(),
        audio_sources=audio_config.available_sources(),
        audio_output_courant=alsa_io.current_output(),
        audio_rate=config.AUDIO_RATE_HZ,
    )


@app.route("/api/settings")
def api_settings_get():
    """Retourne la configuration et le statut actuel en JSON."""
    current_config = config.get_all_config()
    gpio_status = gpio_io.get_current_status()
    sound_card_available = audio_io.sound_card_available()
    
    return jsonify({
        "params": current_config,
        "status": {
            "sound_card": {
                "name": config.SOUND_CARD,
                "available": sound_card_available,
            },
            "gpio": gpio_status,
            "gpio_available": gpio_io.is_gpio_available(),
        },
    })


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    """Met à jour les paramètres de configuration."""
    # Vérifier que l'utilisateur est admin
    if not session.get("is_admin"):
        return jsonify({"erreur": "Seul un administrateur peut modifier les paramètres"}), 403
    
    data = request.get_json(silent=True) or request.form
    new_values = {}
    
    # Extraire les valeurs du formulaire ou JSON
    for param_name in config.MODIFIABLE_PARAMS:
        if param_name in data:
            new_values[param_name] = data[param_name]
    
    if not new_values:
        return jsonify({"erreur": "Aucun paramètre à mettre à jour"}), 400
    
    # Mettre à jour la configuration
    result = config.update_config(new_values)
    
    if not result["ok"]:
        return jsonify({"erreur": result["erreur"]}), 500
    
    # Retourner le résultat avec un avertissement si redémarrage nécessaire
    response = {
        "ok": True,
        "message": "Paramètres sauvegardés avec succès.",
        "params_modifies": result["params_modifies"],
    }
    
    if result["redemarrage_necessaire"]:
        response["message"] += " Certains paramètres nécessitent un redémarrage du service pour prendre effet."
        response["redemarrage_necessaire"] = True
    
    return jsonify(response)


@app.route("/api/gpio-status")
def api_gpio_status():
    """Retourne l'état actuel des GPIO en JSON."""
    gpio_status = gpio_io.get_current_status()
    
    return jsonify({
        "gpio": gpio_status,
        "gpio_available": gpio_io.is_gpio_available(),
    })


# --- Choix et conversion des fichiers audio (§4.2, §5.2) ----------------


def _audio_payload(extra: dict = None) -> dict:
    """État complet des rôles, renvoyé après chaque action pour rafraîchir l'UI."""
    payload = {
        "roles": audio_config.mapping_status(),
        "sources": audio_config.available_sources(),
    }
    if extra:
        payload.update(extra)
    return payload


def _refuse_si_communication():
    """Refuse une conversion en pleine communication : réponse Flask ou None.

    Décoder une dizaine de fichiers sature le Pi Zero 2 W ; le faire pendant
    qu'un invité laisse son message provoquerait des ratés à l'enregistrement.
    Même esprit que le garde-fou de transcribe_batch.py.
    """
    etat = (status_io.read_status() or {}).get("etat")
    if etat not in (None, "attente", "erreur"):
        return jsonify({"erreur": f"Téléphone en cours d'utilisation (état « {etat} ») : "
                                  "réessayez une fois raccroché."}), 409
    return None


@app.route("/api/audio/sources")
def api_audio_sources():
    """Liste les fichiers d'audio_src/ et l'état de chaque rôle (§4.2)."""
    return jsonify(_audio_payload())


@app.route("/api/audio/roles", methods=["POST"])
def api_audio_roles():
    """Enregistre le choix de fichier de chaque rôle, puis convertit (§4.2).

    Seuls les rôles réellement modifiés sont reconvertis : changer une
    sonnerie ne doit pas redécoder les douze messages.
    """
    if not session.get("is_admin"):
        return jsonify({"erreur": "Seul un administrateur peut modifier les fichiers audio"}), 403

    data = request.get_json(silent=True) or request.form
    mapping = {nom: data[nom] for nom in audio_config.ROLE_NAMES if nom in data}
    if not mapping:
        return jsonify({"erreur": "Aucun rôle à modifier"}), 400

    refus = _refuse_si_communication()
    if refus is not None:
        return refus

    import prepare_audio

    resultat = audio_config.set_role_sources(mapping)
    if not resultat["ok"]:
        return jsonify({"erreur": resultat["erreur"]}), 400

    if not resultat["roles_modifies"]:
        return jsonify(_audio_payload({
            "ok": True, "resultats": {},
            "message": "Aucun changement : rien à convertir.",
        }))

    conversion = prepare_audio.prepare_all_locked(resultat["roles_modifies"])
    if conversion.get("occupe"):
        return jsonify({"erreur": conversion["message"]}), 409

    erreurs = conversion["erreurs"]
    message = (f"{len(conversion['generes'])} fichier(s) converti(s)."
               if not erreurs else
               f"{len(erreurs)} conversion(s) en échec : "
               + " ; ".join(f"{role} — {msg}" for role, msg in erreurs.items()))

    return jsonify(_audio_payload({
        "ok": not erreurs,
        "resultats": conversion["resultats"],
        "message": message,
    }))


@app.route("/api/audio/reconvert", methods=["POST"])
def api_audio_reconvert():
    """Reconvertit tous les rôles (ou ceux demandés) depuis audio_src/ (§4.2).

    Utile après une synchronisation Drive, qui remplace des sources sans
    passer par le formulaire.
    """
    if not session.get("is_admin"):
        return jsonify({"erreur": "Seul un administrateur peut reconvertir les fichiers audio"}), 403

    refus = _refuse_si_communication()
    if refus is not None:
        return refus

    import prepare_audio

    data = request.get_json(silent=True) or {}
    demandes = data.get("roles") or None
    if demandes is not None:
        inconnus = [r for r in demandes if r not in audio_config.ROLE_NAMES]
        if inconnus:
            return jsonify({"erreur": f"Rôle(s) inconnu(s) : {', '.join(inconnus)}"}), 400

    conversion = prepare_audio.prepare_all_locked(demandes)
    if conversion.get("occupe"):
        return jsonify({"erreur": conversion["message"]}), 409

    erreurs = conversion["erreurs"]
    message = (f"{len(conversion['generes'])} fichier(s) converti(s), "
               f"{len(conversion['ignores'])} rôle(s) sans source.")
    if erreurs:
        message += (" Échecs : "
                    + " ; ".join(f"{role} — {msg}" for role, msg in erreurs.items()))

    return jsonify(_audio_payload({
        "ok": not erreurs,
        "resultats": conversion["resultats"],
        "message": message,
    }))


@app.route("/api/audio/test", methods=["POST"])
def api_audio_test():
    """Joue un fichier généré sur sa sortie, pour vérifier le câblage (§7.6)."""
    if not session.get("is_admin"):
        return jsonify({"erreur": "Seul un administrateur peut lancer un test audio"}), 403

    refus = _refuse_si_communication()
    if refus is not None:
        return refus

    import prepare_audio

    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in audio_config.ROLE_NAMES:
        return jsonify({"erreur": f"Rôle inconnu : {role}"}), 400

    cible = audio_config.target_for(role)
    if cible is None or not cible.exists():
        return jsonify({"erreur": f"{role} n'a pas encore été converti."}), 400

    sortie = data.get("output") or prepare_audio.output_for(role)
    if sortie not in alsa_io.OUTPUTS:
        return jsonify({"erreur": f"Sortie inconnue : {sortie}"}), 400

    resultat = audio_io.play(cible, should_continue=lambda: True,
                             timeout_sec=config.AUDIO_PLAY_TIMEOUT_SEC,
                             output=sortie)
    if resultat != "completed":
        return jsonify({"erreur": f"Lecture de {cible.name} : {resultat}"}), 500
    return jsonify({"ok": True, "message": f"{cible.name} joué sur {sortie}."})


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
