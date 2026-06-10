import asyncio
import socket
import re
import aiohttp
from html import escape


KNOWN_DDOS_PROVIDERS = {
    "cloudflare": "Cloudflare",
    "akamai": "Akamai",
    "radware": "Radware",
    "arbor": "Arbor Networks / NETSCOUT",
    "netscout": "NETSCOUT",
    "corero": "Corero",
    "imperva": "Imperva",
    "incapsula": "Imperva Incapsula",
    "f5": "F5 Silverline",
    "silverline": "F5 Silverline",
    "voxility": "Voxility",
    "path network": "Path Network",
    "ddos-guard": "DDoS-Guard",
    "ddosguard": "DDoS-Guard",
    "nexusguard": "Nexusguard",
    "black lotus": "Black Lotus",
    "stackpath": "StackPath",
    "sucuri": "Sucuri",
    "fastly": "Fastly",
    "verisign": "Verisign DDoS",
    "link11": "Link11",
    "prolexic": "Akamai Prolexic",
    "telia": "Telia Carrier",
    "limelight": "Limelight Networks",
    "cdnetworks": "CDNetworks",
}

PROTECTION_KEYWORDS = [
    "ddos", "scrub", "clean", "mitigation", "protect", "guard",
    "shield", "filter", "secure", "waf", "anti-ddos", "anti_ddos",
]

KNOWN_DDOS_ASNS = {
    "13335": "Cloudflare",
    "20940": "Akamai",
    "16625": "Akamai",
    "19551": "Incapsula / Imperva",
    "3223": "Voxility",
    "396303": "Path Network",
    "136557": "DDoS-Guard",
    "57724": "DDoS-Guard",
    "62240": "Prolexic / Akamai",
    "209": "CenturyLink / Lumen",
    "6939": "Hurricane Electric",
    "60068": "CDN77",
    "32787": "Prolexic",
}


def _is_valid_ip(ip: str) -> bool:
    ip = ip.strip()
    ipv4 = re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip)
    if ipv4:
        parts = ip.split(".")
        return all(0 <= int(p) <= 255 for p in parts)
    ipv6 = re.match(r"^[0-9a-fA-F:]{2,39}$", ip)
    return bool(ipv6)


def _extract_asn(asn_str: str) -> str:
    m = re.search(r"AS(\d+)", asn_str or "")
    return m.group(1) if m else ""


async def _get_ip_info(ip: str, session: aiohttp.ClientSession) -> dict:
    try:
        async with session.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,org,as,isp,reverse",
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            data = await resp.json()
            if data.get("status") == "success":
                return data
    except Exception:
        pass
    return {}


async def _get_bgpview_asn(asn: str, session: aiohttp.ClientSession) -> dict:
    try:
        async with session.get(
            f"https://api.bgpview.io/asn/{asn}",
            timeout=aiohttp.ClientTimeout(total=6),
            headers={"User-Agent": "Mozilla/5.0 ddos-check-bot/1.0"},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", {})
    except Exception:
        pass
    return {}


async def _get_bgpview_upstreams(asn: str, session: aiohttp.ClientSession) -> list:
    try:
        async with session.get(
            f"https://api.bgpview.io/asn/{asn}/upstreams",
            timeout=aiohttp.ClientTimeout(total=6),
            headers={"User-Agent": "Mozilla/5.0 ddos-check-bot/1.0"},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                upstreams = data.get("data", {})
                ipv4 = upstreams.get("ipv4_upstreams", [])
                ipv6 = upstreams.get("ipv6_upstreams", [])
                return ipv4 + ipv6
    except Exception:
        pass
    return []


async def _get_rpki(ip: str, asn: str, session: aiohttp.ClientSession) -> str:
    try:
        async with session.get(
            f"https://stat.ripe.net/data/rpki-validation/data.json?resource={asn}&prefix={ip}/24",
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                status = data.get("data", {}).get("status", "")
                return status
    except Exception:
        pass
    return ""


def _rdns_lookup(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _check_text_for_protection(text: str) -> tuple[bool, str]:
    text_lower = (text or "").lower()
    for keyword, name in KNOWN_DDOS_PROVIDERS.items():
        if keyword in text_lower:
            return True, name
    for kw in PROTECTION_KEYWORDS:
        if kw in text_lower:
            return True, kw
    return False, ""


async def check_ddos_protection(ip: str) -> dict:
    ip = ip.strip()
    result = {
        "ip": ip,
        "valid": _is_valid_ip(ip),
        "country": "",
        "city": "",
        "isp": "",
        "asn_raw": "",
        "asn_num": "",
        "asn_name": "",
        "rdns": "",
        "rpki_status": "",
        "protection_found": False,
        "protection_signals": [],
        "upstream_protection": [],
        "confidence": 0,
        "error": None,
    }

    if not result["valid"]:
        result["error"] = "Некорректный IP-адрес"
        return result

    loop = asyncio.get_event_loop()
    rdns = await loop.run_in_executor(None, _rdns_lookup, ip)
    result["rdns"] = rdns

    async with aiohttp.ClientSession() as session:
        ip_info = await _get_ip_info(ip, session)

        if not ip_info:
            result["error"] = "Не удалось получить информацию об IP"
            return result

        result["country"] = ip_info.get("country", "")
        result["city"] = ip_info.get("city", "")
        result["isp"] = ip_info.get("isp", ip_info.get("org", ""))
        result["asn_raw"] = ip_info.get("as", "")
        result["asn_num"] = _extract_asn(result["asn_raw"])
        result["asn_name"] = ip_info.get("org", "")

        found, name = _check_text_for_protection(result["isp"])
        if found:
            result["protection_signals"].append(f"ISP содержит: «{name}»")

        found2, name2 = _check_text_for_protection(result["asn_name"])
        if found2 and name2 != name:
            result["protection_signals"].append(f"Организация: «{name2}»")

        if result["asn_num"] in KNOWN_DDOS_ASNS:
            prov = KNOWN_DDOS_ASNS[result["asn_num"]]
            result["protection_signals"].append(f"ASN принадлежит {prov}")

        if rdns:
            found3, name3 = _check_text_for_protection(rdns)
            if found3:
                result["protection_signals"].append(f"rDNS указывает на «{name3}»")

        if result["asn_num"]:
            asn_data, upstreams, rpki = await asyncio.gather(
                _get_bgpview_asn(result["asn_num"], session),
                _get_bgpview_upstreams(result["asn_num"], session),
                _get_rpki(ip, result["asn_num"], session),
            )

            result["rpki_status"] = rpki

            if asn_data:
                asn_desc = asn_data.get("description_short", "") or asn_data.get("name", "")
                found4, name4 = _check_text_for_protection(asn_desc)
                if found4:
                    result["protection_signals"].append(f"Описание ASN: «{name4}»")

            for up in upstreams[:10]:
                up_asn = str(up.get("asn", ""))
                up_name = up.get("name", "") or up.get("description", "")
                if up_asn in KNOWN_DDOS_ASNS:
                    entry = f"{KNOWN_DDOS_ASNS[up_asn]} (AS{up_asn})"
                    if entry not in result["upstream_protection"]:
                        result["upstream_protection"].append(entry)
                else:
                    found5, name5 = _check_text_for_protection(up_name)
                    if found5:
                        entry = f"{name5} (AS{up_asn})"
                        if entry not in result["upstream_protection"]:
                            result["upstream_protection"].append(entry)

    signals = len(result["protection_signals"])
    upstream = len(result["upstream_protection"])

    if signals >= 2:
        result["confidence"] = 90
    elif signals == 1:
        result["confidence"] = 60
    elif upstream >= 1:
        result["confidence"] = 40
    else:
        result["confidence"] = 5

    result["protection_found"] = result["confidence"] >= 40

    return result


def format_ddos_report(data: dict) -> str:
    if data.get("error"):
        return f"❌ <b>Ошибка:</b> {escape(data['error'])}"

    ip = escape(data["ip"])
    isp = escape(data.get("isp", "—"))
    asn = escape(data.get("asn_raw", "—"))
    country = escape(data.get("country", ""))
    city = escape(data.get("city", ""))
    rdns = escape(data.get("rdns", ""))
    rpki = data.get("rpki_status", "")
    confidence = data.get("confidence", 0)
    signals = data.get("protection_signals", [])
    upstream = data.get("upstream_protection", [])
    found = data.get("protection_found", False)

    loc = country
    if city:
        loc = f"{city}, {country}"

    if found:
        if confidence >= 80:
            verdict_line = "🟢 <b>Защита от DDoS ОБНАРУЖЕНА</b>"
        else:
            verdict_line = "🟡 <b>Возможная защита от DDoS</b>"
    else:
        verdict_line = "🔴 <b>Защита от DDoS НЕ обнаружена</b>"

    lines = [
        f"🛡️ <b>Проверка защиты от DDoS</b>",
        f"<code>{ip}</code>\n",
        verdict_line,
        f"📊 Уверенность: <b>{confidence}%</b>\n",
        "─" * 22,
        "<b>Информация об IP:</b>",
        f"  📍 Локация: {loc or '—'}",
        f"  🏢 Провайдер: {isp}",
        f"  🔗 ASN: {asn}",
    ]

    if rdns:
        lines.append(f"  🔄 rDNS: <code>{rdns}</code>")

    if rpki:
        rpki_icons = {"valid": "✅ Валидный (RPKI)", "invalid": "❌ Невалидный (RPKI)", "not-found": "⚠️ Не найден (RPKI)"}
        lines.append(f"  📋 RPKI: {rpki_icons.get(rpki, rpki)}")

    if signals:
        lines.append("")
        lines.append("─" * 22)
        lines.append("<b>🔍 Найденные признаки защиты:</b>")
        for s in signals:
            lines.append(f"  ✅ {escape(s)}")

    if upstream:
        lines.append("")
        lines.append("<b>🌐 Защита через upstream-провайдеров:</b>")
        for u in upstream[:5]:
            lines.append(f"  🔹 {escape(u)}")

    lines.append("")
    lines.append("─" * 22)

    if found:
        if confidence >= 80:
            lines.append(
                "💡 <b>Вывод:</b> IP-адрес проходит через инфраструктуру DDoS-защиты. "
                "Провайдер скорее всего <b>не врёт</b>."
            )
        else:
            lines.append(
                "💡 <b>Вывод:</b> Обнаружены косвенные признаки защиты. "
                "Рекомендуется уточнить у провайдера детали конфигурации."
            )
    else:
        lines.append(
            "💡 <b>Вывод:</b> Признаков DDoS-защиты не обнаружено. "
            "Провайдер может <b>вводить в заблуждение</b> насчёт этой услуги.\n\n"
            "⚠️ Обратите внимание: некоторые провайдеры используют собственную "
            "инфраструктуру без публичных признаков — уточните технические детали напрямую."
        )

    return "\n".join(lines)
