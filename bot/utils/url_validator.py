import ipaddress
import re
import socket
from urllib.parse import urlparse


BLOCKED_SUFFIXES = (
    ".gov", ".mil", ".gov.ru", ".mil.ru",
    ".gov.uk", ".mil.uk", ".gov.ua", ".gov.de",
    ".gouv.fr", ".gob.es", ".gov.au", ".gov.cn",
)

BLOCKED_KEYWORDS = [
    "kremlin", "fsb", "mvd", "cia", "fbi", "nsa", "pentagon",
    "whitehouse", "gov", "mil",
]

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in PRIVATE_NETWORKS)
    except ValueError:
        return False


def validate_target_url(url: str) -> tuple[bool, str]:
    if not url or len(url) > 500:
        return False, "URL слишком длинный или пустой."

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Неверный формат URL."

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "Не удалось определить хост."

    hostname_lower = hostname.lower()

    for suffix in BLOCKED_SUFFIXES:
        if hostname_lower.endswith(suffix):
            return False, f"Домен {suffix} заблокирован."

    for kw in BLOCKED_KEYWORDS:
        if kw in hostname_lower and any(hostname_lower.endswith(s) for s in (".gov", ".mil", ".gov.ru")):
            return False, "Этот домен заблокирован."

    try:
        ip_str = socket.gethostbyname(hostname)
    except socket.gaierror:
        return False, f"Не удалось разрешить хост: {hostname}"

    if _is_private_ip(ip_str):
        return False, "Цель указывает на приватную сеть — тестирование запрещено."

    localhost_patterns = [
        r"^localhost$",
        r"^127\.",
        r"^0\.0\.0\.0$",
    ]
    for pat in localhost_patterns:
        if re.match(pat, ip_str):
            return False, "Тестирование localhost запрещено."

    return True, url
