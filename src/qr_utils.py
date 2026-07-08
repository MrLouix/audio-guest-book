"""Génération des QR codes du dashboard (§5.2) : URL stable + WiFi de l'AP.

Toujours encoder l'URL mDNS (jamais une IP brute, §5.2) : elle survit aux
bascules wifi/AP puisque le hostname reste `livredor.local` dans les deux
cas. Les QR sont rendus en data URI (PNG en base64) pour rester
entièrement autonomes côté client, sans route ni fichier image séparés.
"""

import base64
import io
import re

import qrcode

import config
import network_info


def dashboard_url() -> str:
    """URL stable du dashboard : mDNS si activé (§5.2), sinon repli sur l'IP locale actuelle."""
    host = f"{config.MDNS_HOSTNAME}.local" if config.USE_MDNS else network_info.local_ip()
    return f"http://{host}:{config.WEB_PORT}/"


def _escape_wifi_field(value: str) -> str:
    """Échappe les caractères spéciaux du format QR WiFi (`\\;,:"`)."""
    return re.sub(r'([\\;,:"])', r"\\\1", value)


def wifi_qr_payload(ssid: str, password: str) -> str:
    """Charge utile au format standard `WIFI:...` reconnu nativement par iOS/Android."""
    auth_type = "WPA" if password else "nopass"
    payload = f"WIFI:T:{auth_type};S:{_escape_wifi_field(ssid)};"
    if password:
        payload += f"P:{_escape_wifi_field(password)};"
    payload += ";"
    return payload


def qr_data_uri(data: str) -> str:
    """Encode data en QR code PNG, retourné directement en data URI base64."""
    image = qrcode.make(data)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
