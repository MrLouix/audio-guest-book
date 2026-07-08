"""Détection best-effort du mode réseau (wifi/AP) et de l'IP courante (§5.2).

Implémentation minimale utilisée par le dashboard pour l'affichage. La
bascule wifi/AP elle-même (nmcli, blacklist, surveillance de qualité) sera
implémentée par wifi_or_ap.sh au Sprint 8 ; ce module ne fait aucune
hypothèse sur son fonctionnement, il se contente de lire l'état réseau
courant du système.
"""

import logging
import socket

import config

logger = logging.getLogger(__name__)


def local_ip() -> str:
    """Meilleure estimation de l'IP locale actuelle (aucune connexion réseau réelle requise)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            # UDP "connect" : ne transmet rien sur le réseau, sert seulement à
            # faire choisir par le noyau la route/l'IP source, y compris en
            # mode point d'accès sans accès internet.
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "inconnue"


def network_mode(ip: str) -> str:
    """'ap' si l'IP correspond au point d'accès local, 'wifi' sinon, 'inconnu' si indéterminable."""
    if ip == "inconnue":
        return "inconnu"
    return "ap" if ip == config.AP_IP else "wifi"


def get_network_info() -> dict:
    ip = local_ip()
    return {"mode": network_mode(ip), "ip": ip}
