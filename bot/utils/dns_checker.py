import socket
import asyncio
import aiohttp
from html import escape
from urllib.parse import urlparse


def _resolve_dns_sync(hostname: str) -> dict:
    result = {"hostname": hostname, "ips": [], "error": None}
    try:
        info = socket.getaddrinfo(hostname, None)
        seen = set()
        for item in info:
            ip = item[4][0]
            if ip not in seen:
                seen.add(ip)
                result["ips"].append(ip)
    except socket.gaierror as e:
        result["error"] = str(e)
    return result


async def _geo_lookup(ip: str, session: aiohttp.ClientSession) -> dict:
    try:
        async with session.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,org,as,isp",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json()
            if data.get("status") == "success":
                return data
    except Exception:
        pass
    return {}


async def dns_lookup(hostname: str) -> dict:
    loop = asyncio.get_event_loop()
    dns = await loop.run_in_executor(None, _resolve_dns_sync, hostname)

    if dns["error"] or not dns["ips"]:
        return dns

    geo_results = []
    async with aiohttp.ClientSession() as session:
        tasks = [_geo_lookup(ip, session) for ip in dns["ips"][:3]]
        geo_results = await asyncio.gather(*tasks)

    enriched = []
    for ip, geo in zip(dns["ips"], geo_results):
        entry = {"ip": ip}
        if geo:
            entry["country"] = geo.get("country", "—")
            entry["city"] = geo.get("city", "")
            entry["isp"] = geo.get("isp", geo.get("org", "—"))
            entry["asn"] = geo.get("as", "—")
        enriched.append(entry)

    # remaining IPs without geo
    for ip in dns["ips"][3:]:
        enriched.append({"ip": ip})

    dns["ip_info"] = enriched
    return dns


async def check_ports(hostname: str, ports: list[int]) -> dict[int, bool]:
    loop = asyncio.get_event_loop()

    async def try_port(port: int) -> tuple[int, bool]:
        try:
            fut = loop.run_in_executor(None, _port_check, hostname, port)
            return port, await asyncio.wait_for(fut, timeout=3)
        except asyncio.TimeoutError:
            return port, False

    results = await asyncio.gather(*[try_port(p) for p in ports])
    return dict(results)


def _port_check(hostname: str, port: int) -> bool:
    try:
        with socket.create_connection((hostname, port), timeout=2):
            return True
    except Exception:
        return False


COMMON_PORTS = {
    21: "FTP",
    22: "SSH",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    443: "HTTPS",
    3306: "MySQL",
    5432: "PostgreSQL",
    6379: "Redis",
    8080: "HTTP-alt",
    8443: "HTTPS-alt",
    27017: "MongoDB",
}


def format_dns_report(hostname: str, dns: dict, ports: dict[int, bool]) -> str:
    lines = [f"🌐 <b>DNS / IP / Порты</b>", f"<code>{escape(hostname)}</code>\n"]

    if dns.get("error"):
        lines.append(f"❌ DNS ошибка: {escape(dns['error'])}")
        return "\n".join(lines)

    ip_info = dns.get("ip_info", [{"ip": ip} for ip in dns["ips"]])
    lines.append("<b>IP-адреса:</b>")
    for entry in ip_info:
        ip = entry["ip"]
        if "country" in entry:
            loc = f"{entry['country']}"
            if entry.get("city"):
                loc += f", {entry['city']}"
            isp = entry.get("isp", "")
            lines.append(f"  🔹 <code>{escape(ip)}</code>")
            lines.append(f"     📍 {escape(loc)}")
            if isp:
                lines.append(f"     🏢 {escape(isp)}")
            if entry.get("asn"):
                lines.append(f"     🔗 {escape(entry['asn'])}")
        else:
            lines.append(f"  🔹 <code>{escape(ip)}</code>")

    if ports:
        lines.append("\n<b>Открытые порты:</b>")
        open_ports = [(p, name) for p, name in COMMON_PORTS.items() if ports.get(p)]
        closed_critical = [(p, name) for p, name in [(80, "HTTP"), (443, "HTTPS")] if not ports.get(p)]

        if open_ports:
            for port, name in open_ports:
                risk = ""
                if port in (3306, 5432, 6379, 27017):
                    risk = " ⚠️ открыт наружу!"
                lines.append(f"  🟢 {port} ({name}){risk}")
        else:
            lines.append("  Нет открытых из списка")

        if closed_critical:
            for port, name in closed_critical:
                lines.append(f"  🔴 {port} ({name}) — закрыт")

    return "\n".join(lines)
