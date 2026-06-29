import ipaddress
import os
import re
import socket
from urllib.parse import urlparse


BLOCKED_SUFFIXES = (
    ".gov", ".mil", ".gov.ru", ".mil.ru",
    ".gov.uk", ".mil.uk", ".gov.ua", ".gov.de",
    ".gouv.fr", ".gob.es", ".gov.au", ".gov.cn",
    ".gov.br", ".gov.in", ".gov.jp", ".gov.kr",
    ".gov.tr", ".gov.it", ".gov.pl", ".gc.ca",
)

BLOCKED_KEYWORDS = [
    "kremlin", "fsb", "mvd", "cia", "fbi", "nsa", "pentagon",
    "whitehouse", "gov", "mil",
]

# Hardcoded protected domains — critical infrastructure
HARDCODED_BLACKLIST = {
    # Payment systems
    "visa.com", "mastercard.com", "paypal.com", "stripe.com",
    "qiwi.ru", "yoomoney.ru", "sberbank.ru", "vtb.ru", "tinkoff.ru",
    "alfabank.ru", "gazprombank.ru", "raiffeisen.ru",
    # Major platforms (abuse potential)
    "telegram.org", "telegram.me", "t.me",
    "google.com", "youtube.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "amazon.com",
    "microsoft.com", "apple.com", "cloudflare.com",
    # Russian government / law enforcement
    "fsb.ru", "mvd.ru", "kremlin.ru", "government.ru",
    "prosecutor.ru", "sledcom.ru",
    # Emergency / healthcare
    "112.ru", "gosuslugi.ru",
}

def _load_env_blacklist() -> set:
    """Load additional blocked domains from BLACKLIST_DOMAINS env var (comma-separated)."""
    raw = os.environ.get("BLACKLIST_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


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

    # Strip www. prefix for blacklist lookup
    bare = hostname_lower.removeprefix("www.")

    # Hardcoded blacklist
    env_blacklist = _load_env_blacklist()
    all_blocked = HARDCODED_BLACKLIST | env_blacklist
    if bare in all_blocked or hostname_lower in all_blocked:
        return False, f"Домен {hostname} заблокирован политикой сервиса."

    # Also block subdomains of blacklisted domains
    for blocked in all_blocked:
        if hostname_lower.endswith("." + blocked):
            return False, f"Домен {hostname} заблокирован (поддомен защищённого ресурса)."

    for suffix in BLOCKED_SUFFIXES:
        if hostname_lower.endswith(suffix):
            return False, f"Домен с суффиксом {suffix} заблокирован."

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
