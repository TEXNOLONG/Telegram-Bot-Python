import ssl
import socket
import asyncio
from datetime import datetime, timezone
from html import escape


def _check_ssl_sync(hostname: str) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=10) as raw:
            with ctx.wrap_socket(raw, server_hostname=hostname) as s:
                cert = s.getpeercert()
                cipher = s.cipher()
                proto = s.version()

        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))

        not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days

        sans = []
        for typ, val in cert.get("subjectAltName", []):
            if typ == "DNS":
                sans.append(val)

        return {
            "valid": True,
            "subject_cn": subject.get("commonName", "—"),
            "issuer_org": issuer.get("organizationName", issuer.get("commonName", "—")),
            "not_before": not_before.strftime("%d.%m.%Y"),
            "not_after": not_after.strftime("%d.%m.%Y"),
            "days_left": days_left,
            "protocol": proto or "—",
            "cipher": cipher[0] if cipher else "—",
            "sans": sans[:8],
            "san_count": len(sans),
        }
    except ssl.SSLCertVerificationError as e:
        return {"valid": False, "error": f"Сертификат недействителен: {e.reason}"}
    except ssl.SSLError as e:
        return {"valid": False, "error": f"SSL ошибка: {e}"}
    except ConnectionRefusedError:
        return {"valid": False, "error": "Порт 443 закрыт — HTTPS не настроен"}
    except socket.timeout:
        return {"valid": False, "error": "Таймаут подключения"}
    except OSError as e:
        return {"valid": False, "error": f"Нет соединения: {e}"}


async def check_ssl(hostname: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_ssl_sync, hostname)


def format_ssl_report(hostname: str, data: dict) -> str:
    if not data.get("valid"):
        return (
            f"🔐 <b>SSL-сертификат: {escape(hostname)}</b>\n\n"
            f"❌ <b>Ошибка:</b> {escape(data.get('error', 'Неизвестно'))}"
        )

    days = data["days_left"]
    if days > 60:
        exp_icon = "🟢"
        exp_label = f"действует ещё {days} дн."
    elif days > 14:
        exp_icon = "🟡"
        exp_label = f"истекает через {days} дн."
    elif days > 0:
        exp_icon = "🟠"
        exp_label = f"⚠️ истекает через {days} дн.!"
    else:
        exp_icon = "🔴"
        exp_label = "просрочен!"

    lines = [
        f"🔐 <b>SSL-сертификат</b>",
        f"<code>{escape(hostname)}</code>\n",
        f"✅ Сертификат действителен\n",
        f"<b>Кому выдан:</b> {escape(data['subject_cn'])}",
        f"<b>Выдан:</b> {escape(data['issuer_org'])}",
        f"<b>Действует с:</b> {data['not_before']}",
        f"<b>Действует до:</b> {data['not_after']}",
        f"{exp_icon} <b>Срок:</b> {exp_label}\n",
        f"<b>Протокол:</b> {escape(data['protocol'])}",
        f"<b>Шифр:</b> <code>{escape(data['cipher'])}</code>",
    ]

    if data["sans"]:
        lines.append(f"\n<b>Домены ({data['san_count']}):</b>")
        for s in data["sans"]:
            lines.append(f"  • <code>{escape(s)}</code>")
        if data["san_count"] > 8:
            lines.append(f"  … и ещё {data['san_count'] - 8}")

    return "\n".join(lines)
