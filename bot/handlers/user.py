import asyncio
import logging
import socket
import uuid
from html import escape
from urllib.parse import urlparse

import aiohttp
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import DOMAIN
from bot.keyboards import (
    back_to_menu_kb, cancel_kb, main_menu_kb,
    register_kb, stress_lite_kb, stress_pro_kb, stress_flood_kb,
    subscription_menu_kb,
)
from bot.storage import storage
from bot.utils.url_validator import validate_target_url
from bot.utils.site_analyzer import analyze_site, format_report
from bot.utils.ssl_checker import check_ssl, format_ssl_report
from bot.utils.dns_checker import dns_lookup, check_ports, format_dns_report
from bot.utils.ddos_checker import check_ddos_protection, format_ddos_report
from bot.utils.traffic_worker import enqueue_task
from bot.db import get_session
from bot.models import Report

logger = logging.getLogger(__name__)
router = Router()


class UserStates(StatesGroup):
    waiting_for_url = State()
    ssl_waiting_url = State()
    dns_waiting_url = State()
    ping_waiting_url = State()
    whois_waiting_url = State()
    geoip_waiting = State()
    stress_waiting_url = State()
    stress_pro_waiting_url = State()
    stress_flood_waiting_url = State()


def _report_url(report_id: str) -> str:
    return f"https://{DOMAIN}/report/{report_id}"


def _report_kb(report_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Открыть отчёт", url=_report_url(report_id))],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
    ])


async def _delete_and_send(cb: CallbackQuery, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    """Delete the current message and send a new one (clean back-button UX)."""
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def _resolve_ip(host: str) -> str | None:
    """Resolve hostname to IP address."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, socket.gethostbyname, host)
        return result
    except Exception:
        return None


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id

    if storage.is_banned(uid):
        await message.answer("🚫 Вы заблокированы.")
        return

    storage.upsert_user(uid, message.from_user.first_name, message.from_user.username)
    is_registered = storage.is_web_registered(uid)
    is_pro = storage.is_pro(uid)
    banner = storage.get_banner()

    if not is_registered:
        text = (
            f"👋 <b>Привет, {escape(message.from_user.first_name)}!</b>\n\n"
            "LoadTest Pro — профессиональный инструмент анализа и нагрузочного тестирования.\n\n"
            "Для начала пройдите регистрацию:"
        )
        kb = register_kb(uid, DOMAIN)
    else:
        tier = "👑 PRO" if is_pro else "🆓 LITE"
        expires = storage.sub_expires_str(uid)
        exp_line = f"\n📅 Подписка до: <b>{expires}</b>" if expires and is_pro else ""
        free_left = storage.free_left(uid)
        text = (
            f"👋 <b>Привет, {escape(message.from_user.first_name)}!</b>\n\n"
            f"💼 Тариф: <b>{tier}</b>{exp_line}"
            + (f"\n🆓 Осталось бесплатных: <b>{free_left}</b>" if not is_pro else "")
        )
        kb = main_menu_kb(has_sub=is_pro, is_registered=True)

    if banner:
        await message.answer_photo(photo=banner, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ─── Menu ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu", "main_menu", "menu_back"}))
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    is_reg = storage.is_web_registered(uid)
    free_left = storage.free_left(uid) if not is_pro else None
    free_line = f"\n🆓 Бесплатных сегодня: <b>{free_left}</b>" if free_left is not None else ""
    await _delete_and_send(
        cb,
        f"🏠 <b>Главное меню</b>{free_line}",
        reply_markup=main_menu_kb(is_pro, is_reg),
    )
    await cb.answer()


@router.callback_query(F.data == "need_reg")
async def cb_need_reg(cb: CallbackQuery):
    uid = cb.from_user.id
    await _delete_and_send(
        cb,
        "📋 <b>Требуется регистрация</b>\n\nНажмите кнопку для регистрации:",
        reply_markup=register_kb(uid, DOMAIN),
    )
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    try:
        await cb.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery):
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    user = storage.get_user(uid)
    expires = storage.sub_expires_str(uid)
    free_left = storage.free_left(uid) if not is_pro else "∞"
    analyses = (user or {}).get("total_analyses", 0)
    is_reg = storage.is_web_registered(uid)

    tier = "👑 PRO" if is_pro else "🆓 LITE"
    exp_line = f"\n📅 До: <b>{expires}</b>" if expires and is_pro else ""
    reg_line = "✅ да" if is_reg else "❌ нет"

    await _delete_and_send(
        cb,
        f"📊 <b>Статус аккаунта</b>\n\n"
        f"💼 Тариф: <b>{tier}</b>{exp_line}\n"
        f"📋 Регистрация: {reg_line}\n"
        f"🆓 Бесплатных сегодня: <b>{free_left}</b>\n"
        f"🔢 Всего проверок: <b>{analyses}</b>",
        reply_markup=back_to_menu_kb(),
    )
    await cb.answer()


# ─── Site analysis ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "analyze")
async def cb_analyze(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if not storage.can_analyze(uid):
        limit = storage.get_free_limit()
        await cb.answer(f"Лимит {limit}/день исчерпан. Нужна PRO-подписка.", show_alert=True)
        return
    await state.set_state(UserStates.waiting_for_url)
    await _delete_and_send(
        cb,
        "🔍 <b>Анализ сайта</b>\n\n"
        "Отправьте URL сайта — проверю:\n"
        "SEO · SSL · Безопасность · Производительность\n"
        "DNS · Технологии · Доступность · Контент",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.waiting_for_url)
async def handle_analyze_url(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    url = message.text.strip()

    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    if not storage.can_analyze(uid):
        await message.answer("🚫 Лимит анализов исчерпан.")
        return

    msg = await message.answer(
        "🔍 <b>Анализирую сайт...</b>\n\n"
        "<i>SEO · SSL · Заголовки · DNS · Технологии · Контент...</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        result = await analyze_site(url_or_err)
    except Exception as e:
        try:
            await msg.edit_text(f"❌ Ошибка анализа: {escape(str(e)[:200])}")
        except Exception:
            pass
        return

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)
    else:
        storage.record_analysis(uid)

    report_id = str(uuid.uuid4())
    data = dict(result)
    data["target_url"] = url_or_err

    try:
        with get_session() as session:
            session.add(Report(
                report_id=report_id,
                user_id=uid,
                report_type="analysis",
                target_url=url_or_err,
                data=data,
            ))
    except Exception as e:
        logger.error("Failed to save report: %s", e)

    score = result.get("score", 0)
    try:
        storage.add_history(uid, url_or_err, score, report_id)
    except Exception:
        pass

    try:
        short_report = format_report(result)
        if len(short_report) > 3500:
            short_report = short_report[:3500] + "\n\n<i>…полный анализ в отчёте</i>"

        await msg.edit_text(
            short_report,
            parse_mode=ParseMode.HTML,
            reply_markup=_report_kb(report_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Failed to send analysis result: %s", e)
        # Fallback — send just the link
        try:
            await msg.edit_text(
                f"✅ <b>Анализ завершён!</b>\n\n"
                f"🌐 <code>{escape(url_or_err)}</code>\n"
                f"⭐ Оценка: <b>{score}/100</b>\n\n"
                f"📋 Полный отчёт по ссылке ниже:",
                parse_mode=ParseMode.HTML,
                reply_markup=_report_kb(report_id),
            )
        except Exception:
            pass


# ─── SSL ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ssl")
async def cb_ssl(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ssl_waiting_url)
    await _delete_and_send(
        cb,
        "🔐 <b>Проверка SSL</b>\n\nОтправьте домен (без http/https):",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.ssl_waiting_url)
async def handle_ssl_url(message: Message, state: FSMContext):
    await state.clear()
    host = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    msg = await message.answer("🔐 Проверяю SSL...")
    try:
        result = await check_ssl(host)
        text = format_ssl_report(host, result)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {escape(str(e)[:200])}", reply_markup=back_to_menu_kb())


# ─── DNS ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "dns")
async def cb_dns(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dns_waiting_url)
    await state.update_data(mode="dns")
    await _delete_and_send(
        cb,
        "🌐 <b>DNS / IP анализ</b>\n\nОтправьте домен или IP-адрес:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


# ─── DDoS check ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ddos_check")
async def cb_ddos(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dns_waiting_url)
    await state.update_data(mode="ddos")
    await _delete_and_send(
        cb,
        "🛡 <b>Проверка DDoS-защиты</b>\n\nОтправьте домен или IP:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.dns_waiting_url)
async def handle_dns_or_ddos(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    mode = data.get("mode", "dns")
    host = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    msg = await message.answer("🔍 Проверяю...")
    try:
        if mode == "ddos":
            result = await check_ddos_protection(host)
            text = format_ddos_report(result)
        else:
            dns_data = await dns_lookup(host)
            ports_data = await check_ports(host, [80, 443, 8080, 8443, 22, 21, 3306, 5432, 6379, 27017])
            text = format_dns_report(host, dns_data, ports_data)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {escape(str(e)[:200])}", reply_markup=back_to_menu_kb())


# ─── Ping ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ping_tool")
async def cb_ping(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ping_waiting_url)
    await _delete_and_send(
        cb,
        "⏱ <b>Ping / Скорость ответа</b>\n\nОтправьте URL или домен:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.ping_waiting_url)
async def handle_ping(message: Message, state: FSMContext):
    await state.clear()
    raw = message.text.strip()
    if not raw.startswith("http"):
        raw = "http://" + raw
    host = urlparse(raw).hostname or raw

    msg = await message.answer("⏱ Измеряю время ответа...")
    results = []
    errors = 0
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            for i in range(5):
                t0 = asyncio.get_event_loop().time()
                try:
                    async with session.get(
                        raw,
                        timeout=aiohttp.ClientTimeout(total=8),
                        allow_redirects=True,
                        ssl=False,
                    ) as resp:
                        await resp.read()
                        elapsed = (asyncio.get_event_loop().time() - t0) * 1000
                        results.append((elapsed, resp.status))
                except Exception:
                    errors += 1
                await asyncio.sleep(0.3)
    except Exception:
        pass

    if not results:
        await msg.edit_text("❌ Сервер недоступен или не отвечает.", reply_markup=back_to_menu_kb())
        return

    times = [r[0] for r in results]
    avg = sum(times) / len(times)
    mn, mx = min(times), max(times)
    status = results[0][1] if results else 0
    jitter = mx - mn

    grade = "🟢 Отлично" if avg < 200 else "🟡 Нормально" if avg < 800 else "🔴 Медленно"

    lines = [
        f"⏱ <b>Ping: {escape(host)}</b>",
        "",
        f"📡 Статус: <b>{status}</b>",
        f"📊 Среднее: <b>{avg:.0f} мс</b>   {grade}",
        f"⬇️ Мин: <b>{mn:.0f} мс</b>   ⬆️ Макс: <b>{mx:.0f} мс</b>",
        f"〰️ Джиттер: <b>{jitter:.0f} мс</b>",
        f"✅ Пакетов: <b>{len(results)}/5</b>   ❌ Потерь: <b>{errors}</b>",
        "",
    ]
    for i, (t, s) in enumerate(results, 1):
        bar = "▓" * min(int(t / 100), 20)
        lines.append(f"  #{i}: {t:.0f} мс [{s}] {bar}")

    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── Geo-IP ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "geoip")
async def cb_geoip(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.geoip_waiting)
    await _delete_and_send(
        cb,
        "🌍 <b>Гео-IP информация</b>\n\nОтправьте IP-адрес или домен:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.geoip_waiting)
async def handle_geoip(message: Message, state: FSMContext):
    await state.clear()
    raw = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    msg = await message.answer("🌍 Получаю данные...")

    # Resolve to IP if domain
    ip = raw
    if not _is_ip(raw):
        ip = await _resolve_ip(raw)
        if not ip:
            await msg.edit_text("❌ Не удалось определить IP для этого домена.", reply_markup=back_to_menu_kb())
            return

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                f"http://ip-api.com/json/{ip}?lang=ru&fields=status,country,regionName,city,zip,lat,lon,isp,org,as,hosting,proxy,mobile",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                geo = await resp.json()
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {escape(str(e)[:100])}", reply_markup=back_to_menu_kb())
        return

    if geo.get("status") != "success":
        await msg.edit_text("❌ Не удалось получить данные по этому IP.", reply_markup=back_to_menu_kb())
        return

    flags = []
    if geo.get("hosting"):
        flags.append("🏢 Хостинг/VPS/DC")
    if geo.get("proxy"):
        flags.append("🔀 Прокси/VPN")
    if geo.get("mobile"):
        flags.append("📱 Мобильная сеть")
    flags_line = "  ".join(flags) if flags else "—"

    text = (
        f"🌍 <b>Гео-IP: {escape(ip)}</b>"
        + (f" ({escape(raw)})" if raw != ip else "")
        + "\n\n"
        f"🏳️ Страна: <b>{escape(geo.get('country', '—'))}</b>\n"
        f"🏙 Регион: <b>{escape(geo.get('regionName', '—'))}</b>\n"
        f"🏢 Город: <b>{escape(geo.get('city', '—'))}</b>\n"
        f"📮 Индекс: <b>{escape(geo.get('zip', '—'))}</b>\n"
        f"📡 ISP: <b>{escape(geo.get('isp', '—'))}</b>\n"
        f"🏢 Организация: <b>{escape(geo.get('org', '—'))}</b>\n"
        f"🔢 ASN: <code>{escape(geo.get('as', '—'))}</code>\n"
        f"📍 Координаты: <code>{geo.get('lat','—')}, {geo.get('lon','—')}</code>\n"
        f"🏷 Тип: {flags_line}"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


# ─── Whois ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "whois")
async def cb_whois(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.whois_waiting_url)
    await _delete_and_send(
        cb,
        "🔎 <b>Whois домена</b>\n\nОтправьте домен (например: google.com):",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.whois_waiting_url)
async def handle_whois(message: Message, state: FSMContext):
    await state.clear()
    domain = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    msg = await message.answer("🔎 Запрашиваю Whois...")

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                f"https://rdap.org/domain/{domain}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}")
                data = await resp.json()
    except Exception as e:
        await msg.edit_text(
            f"❌ Whois недоступен: {escape(str(e)[:100])}\n\n"
            f"Попробуйте: <a href='https://who.is/whois/{escape(domain)}'>who.is</a>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
            disable_web_page_preview=True,
        )
        return

    # Parse RDAP response
    name = data.get("ldhName", domain)
    status = ", ".join(data.get("status", [])) or "—"
    reg_date = "—"
    upd_date = "—"
    exp_date = "—"
    for ev in data.get("events", []):
        action = ev.get("eventAction", "")
        date_val = ev.get("eventDate", "")[:10]
        if action == "registration":
            reg_date = date_val
        elif action == "last changed":
            upd_date = date_val
        elif action == "expiration":
            exp_date = date_val

    registrar = "—"
    registrant = "—"
    for entity in data.get("entities", []):
        roles = entity.get("roles", [])
        vcard = entity.get("vcardArray", [])
        entity_name = ""
        if vcard and len(vcard) > 1:
            for prop in vcard[1]:
                if prop[0] == "fn":
                    entity_name = prop[3]
                    break
        if "registrar" in roles and entity_name:
            registrar = entity_name
        if "registrant" in roles and entity_name:
            registrant = entity_name

    nameservers = [ns.get("ldhName", "") for ns in data.get("nameservers", [])]
    ns_str = "\n".join(f"  • {escape(ns.lower())}" for ns in nameservers[:6]) or "—"

    text = (
        f"🔎 <b>Whois: {escape(name)}</b>\n\n"
        f"📅 Зарегистрирован: <b>{reg_date}</b>\n"
        f"🔄 Обновлён: <b>{upd_date}</b>\n"
        f"⏳ Истекает: <b>{exp_date}</b>\n"
        f"📋 Статус: <code>{escape(status[:100])}</code>\n"
        f"🏢 Регистратор: <b>{escape(registrar[:60])}</b>\n"
        f"👤 Владелец: <b>{escape(registrant[:60])}</b>\n\n"
        f"🌐 Серверы имён:\n{ns_str}"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())


# ─── History ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery):
    uid = cb.from_user.id
    history = storage.get_history(uid)
    if not history:
        await cb.answer("История пуста", show_alert=True)
        return

    lines = ["📋 <b>История проверок</b>\n"]
    for h in history:
        domain = (h["url"] or "")[:40]
        score = h.get("score", "—")
        date = h.get("date", "")
        rid = h.get("report_id")
        if rid:
            lines.append(f"• <a href='{_report_url(rid)}'>{escape(domain)}</a> — {score}pts  <i>{date}</i>")
        else:
            lines.append(f"• {escape(domain)} — {score}pts  <i>{date}</i>")

    await _delete_and_send(cb, "\n".join(lines), reply_markup=back_to_menu_kb())
    await cb.answer()


# ─── Subscription ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"sub", "buy"}))
async def cb_sub(cb: CallbackQuery):
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    expires = storage.sub_expires_str(uid)
    prices = storage.get_prices()

    status = f"✅ Подписка активна до <b>{expires}</b>\n\n" if (is_pro and expires) else ""
    text = (
        f"{status}👑 <b>PRO-подписка</b>\n\n"
        "⚡ Нагрузочные тесты до 2000+ RPS\n"
        "💥 Flood-режим — максимальная нагрузка\n"
        "🤖 Авто-выбор метода атаки по цели\n"
        "🐢 Slowloris / RUDY / Cache Bust\n"
        "🛡 Обход Cloudflare / Akamai / CDN\n"
        "📊 Подробные веб-отчёты с графиками\n"
        "🔄 Ротация User-Agent и сессий\n\n"
        "Выберите план:"
    )
    await _delete_and_send(cb, text, reply_markup=subscription_menu_kb(prices, is_pro, expires))
    await cb.answer()


# ─── LITE stress test ── тест по IP цели ──────────────────────────────────────

@router.callback_query(F.data == "stress_lite")
async def cb_stress_lite(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_web_registered(uid):
        await cb.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    if not storage.can_analyze(uid):
        await cb.answer("Лимит тестов исчерпан.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "⚡ <b>LITE-тест</b>\n\n"
        "Бот определит IP-адрес цели и запустит нагрузочный тест\n"
        "напрямую на IP — обходя CDN и DNS-защиту.\n\n"
        "Выберите длительность:",
        reply_markup=stress_lite_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("lite:"))
async def cb_lite_duration(cb: CallbackQuery, state: FSMContext):
    _, duration, rps = cb.data.split(":")
    await state.set_state(UserStates.stress_waiting_url)
    await state.update_data(duration=int(duration), rps=int(rps))
    await _delete_and_send(cb, "🌐 Отправьте URL или домен для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_waiting_url)
async def handle_lite_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    raw = message.text.strip()

    ok, url_or_err = validate_target_url(raw)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    if not storage.can_analyze(uid):
        await message.answer("🚫 Лимит тестов исчерпан.")
        return

    # Resolve target to IP
    wait_msg = await message.answer("🔍 Определяю IP-адрес цели...")
    parsed = urlparse(url_or_err)
    host = parsed.hostname or url_or_err
    ip = await _resolve_ip(host)

    if not ip:
        await wait_msg.edit_text(
            "❌ Не удалось определить IP-адрес цели. Проверьте домен.",
            reply_markup=cancel_kb(),
        )
        return

    # Build IP-based target URL (bypass CDN)
    scheme = parsed.scheme or "http"
    ip_url = f"{scheme}://{ip}/"

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)

    duration = data.get("duration", 60)
    max_rps = data.get("rps", 100)

    enqueue_task(uid, "load_test", {
        "target_url": ip_url,
        "original_url": url_or_err,
        "mode": "lite",
        "duration": duration,
        "intensity": "low",
        "max_rps": max_rps,
        "method_type": "http_flood",
    })

    await wait_msg.edit_text(
        f"⚡ <b>LITE-тест запущен</b>\n\n"
        f"🌐 Цель: <code>{escape(url_or_err)}</code>\n"
        f"🔢 IP: <code>{ip}</code>  <i>(прямой, без CDN)</i>\n"
        f"⏱ {duration} сек · {max_rps} RPS · HTTP Flood\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── PRO stress test ── авто-режим, только длительность ──────────────────────

@router.callback_query(F.data == "stress_pro")
async def cb_stress_pro(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO-подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "⚡ <b>PRO-тест</b>\n\n"
        "🤖 Бот <b>автоматически</b> определит:\n"
        "  • IP-адрес и обходит CDN\n"
        "  • Лучший метод атаки (Flood / Slowloris / RUDY / Cache Bust)\n"
        "  • Оптимальную интенсивность\n\n"
        "Выберите только <b>длительность</b>:",
        reply_markup=stress_pro_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro:"))
async def cb_pro_duration(cb: CallbackQuery, state: FSMContext):
    duration = int(cb.data.split(":")[1])
    await state.set_state(UserStates.stress_pro_waiting_url)
    await state.update_data(duration=duration)
    await _delete_and_send(cb, "🌐 Отправьте URL или домен для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_pro_waiting_url)
async def handle_pro_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    raw = message.text.strip()

    if not storage.is_pro(uid):
        await message.answer("🚫 Требуется PRO-подписка.")
        return

    ok, url_or_err = validate_target_url(raw)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 120)

    # Intensity based on duration
    intensity_map = {60: "medium", 120: "high", 180: "ultra", 300: "ultra"}
    intensity = intensity_map.get(duration, "high")

    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "pro",
        "duration": duration,
        "intensity": intensity,
        "method_type": "auto",
    })

    label_map = {60: "умеренный", 120: "средний", 180: "высокий", 300: "максимум"}
    level = label_map.get(duration, "высокий")

    await message.answer(
        f"⚡ <b>PRO-тест запущен</b>\n\n"
        f"🌐 <code>{escape(url_or_err)}</code>\n"
        f"⏱ {duration} сек · <b>{level}</b>\n"
        f"🤖 Метод: <b>Авто</b> — бот анализирует цель и выбирает\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── FLOOD stress test ── авто-режим, только длительность ────────────────────

@router.callback_query(F.data == "stress_flood")
async def cb_stress_flood(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO-подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "💥 <b>Flood-тест</b>\n\n"
        "Агрессивный режим: максимальный поток запросов.\n"
        "🤖 Бот автоматически определит лучшую стратегию.\n\n"
        "Выберите длительность:",
        reply_markup=stress_flood_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("flood:"))
async def cb_flood_duration(cb: CallbackQuery, state: FSMContext):
    duration = int(cb.data.split(":")[1])
    await state.set_state(UserStates.stress_flood_waiting_url)
    await state.update_data(duration=duration)
    await _delete_and_send(cb, "🌐 Отправьте URL или домен для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_flood_waiting_url)
async def handle_flood_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    raw = message.text.strip()

    if not storage.is_pro(uid):
        await message.answer("🚫 Требуется PRO-подписка.")
        return

    ok, url_or_err = validate_target_url(raw)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 60)
    intensity_map = {60: "high", 120: "ultra", 180: "ultra"}
    intensity = intensity_map.get(duration, "ultra")

    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "flood",
        "duration": duration,
        "intensity": intensity,
        "method_type": "auto",
    })

    label_map = {60: "агрессивный", 120: "экстремальный", 180: "максимум"}
    level = label_map.get(duration, "максимум")

    await message.answer(
        f"💥 <b>Flood-тест запущен</b>\n\n"
        f"🌐 <code>{escape(url_or_err)}</code>\n"
        f"⏱ {duration} сек · <b>{level}</b>\n"
        f"🤖 Метод: <b>Авто</b> — максимальное давление на сервер\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )
