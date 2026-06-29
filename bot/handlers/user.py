import logging
import uuid

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import ADMIN_ID, DOMAIN, SUBSCRIPTION_PLANS
from bot.keyboards import (
    back_to_menu_kb, cancel_kb, main_menu_kb,
    register_kb, stress_lite_kb, stress_pro_kb, stress_flood_kb,
    subscription_menu_kb, flood_method_kb, pro_method_kb,
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
    stress_waiting_url = State()
    stress_pro_waiting_url = State()
    stress_flood_waiting_url = State()


def _report_url(report_id: str) -> str:
    return f"https://{DOMAIN}/report/{report_id}"


def _report_kb(report_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Открыть отчёт", url=_report_url(report_id))],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_back")],
    ])


async def _delete_and_send(cb: CallbackQuery, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    """Delete the current message and send a new one for a clean look."""
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


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
            f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
            "Для начала работы пройдите регистрацию по кнопке ниже."
        )
        kb = register_kb(uid, DOMAIN)
    else:
        tier = "👑 PRO" if is_pro else "🆓 LITE"
        expires = storage.sub_expires_str(uid)
        exp_line = f"\n📅 Подписка до: <b>{expires}</b>" if expires and is_pro else ""
        text = (
            f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
            f"💼 Тариф: <b>{tier}</b>{exp_line}"
        )
        kb = main_menu_kb(has_sub=is_pro, is_registered=True)

    if banner:
        await message.answer_photo(photo=banner, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.message(Command("register"))
async def cmd_register(message: Message):
    uid = message.from_user.id
    if storage.is_web_registered(uid):
        await message.answer("✅ Вы уже зарегистрированы.", reply_markup=main_menu_kb())
        return
    await message.answer("Нажмите кнопку для регистрации:", reply_markup=register_kb(uid, DOMAIN))


@router.message(Command("status"))
async def cmd_status(message: Message):
    await show_status(message.from_user.id, message)


@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    user = storage.get_user(uid)
    expires = storage.sub_expires_str(uid)
    free_left = storage.free_left(uid) if not is_pro else "∞"
    analyses = (user or {}).get("total_analyses", 0)
    is_reg = storage.is_web_registered(uid)

    tier = "👑 PRO" if is_pro else "🆓 LITE"
    exp_line = f"\n📅 Подписка до: <b>{expires}</b>" if expires and is_pro else ""
    reg_line = "✅ да" if is_reg else "❌ нет"

    text = (
        f"📊 <b>Статус аккаунта</b>\n\n"
        f"💼 Тариф: <b>{tier}</b>{exp_line}\n"
        f"📋 Регистрация: {reg_line}\n"
        f"🆓 Бесплатных сегодня: <b>{free_left}</b>\n"
        f"🔢 Всего проверок: <b>{analyses}</b>"
    )
    await _delete_and_send(cb, text, reply_markup=back_to_menu_kb())


async def show_status(uid: int, target):
    is_pro = storage.is_pro(uid)
    expires = storage.sub_expires_str(uid)
    user = storage.get_user(uid)
    free_left = storage.free_left(uid) if not is_pro else "∞"
    analyses = (user or {}).get("total_analyses", 0)
    tier = "👑 PRO" if is_pro else "🆓 LITE"
    exp_line = f"\n📅 До: <b>{expires}</b>" if expires and is_pro else ""
    text = (
        f"📊 <b>Статус</b>\n\n"
        f"💼 Тариф: <b>{tier}</b>{exp_line}\n"
        f"🆓 Бесплатных сегодня: <b>{free_left}</b>\n"
        f"🔢 Проверок всего: <b>{analyses}</b>"
    )
    await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())


# ─── Menu ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu", "main_menu", "menu_back"}))
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    is_reg = storage.is_web_registered(uid)
    await _delete_and_send(cb, "🏠 <b>Главное меню</b>", reply_markup=main_menu_kb(is_pro, is_reg))
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


# ─── Site analysis ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "analyze")
async def cb_analyze(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if not storage.can_analyze(uid):
        limit = storage.get_free_limit()
        await cb.answer(f"Лимит исчерпан ({limit}/день). Нужна PRO-подписка.", show_alert=True)
        return
    await state.set_state(UserStates.waiting_for_url)
    await _delete_and_send(
        cb,
        "🔍 <b>Анализ сайта</b>\n\n"
        "Отправьте URL сайта для глубокого анализа.\n"
        "Проверяю: SEO, безопасность, производительность, SSL, технологии, доступность и многое другое.",
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
        await message.answer("🚫 Лимит анализов исчерпан. Нужна PRO-подписка.")
        return

    msg = await message.answer(
        "🔍 <b>Анализирую сайт...</b>\n\n"
        "⏳ SEO · Безопасность · Производительность · SSL · DNS · Технологии...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await analyze_site(url_or_err)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")
        return

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)
    else:
        storage.record_analysis(uid)

    report_id = str(uuid.uuid4())
    data = dict(result)
    data["target_url"] = url_or_err

    with get_session() as session:
        session.add(Report(
            report_id=report_id,
            user_id=uid,
            report_type="analysis",
            target_url=url_or_err,
            data=data,
        ))

    score = result.get("score", 0)
    storage.add_history(uid, url_or_err, score, report_id)

    short_report = format_report(result)
    if len(short_report) > 3800:
        short_report = short_report[:3800] + "\n\n<i>...подробнее в отчёте</i>"

    await msg.edit_text(
        short_report,
        parse_mode=ParseMode.HTML,
        reply_markup=_report_kb(report_id),
        disable_web_page_preview=True,
    )


# ─── SSL ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ssl")
async def cb_ssl(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ssl_waiting_url)
    await _delete_and_send(cb, "🔐 <b>Проверка SSL</b>\n\nОтправьте домен:", reply_markup=cancel_kb())
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
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=back_to_menu_kb())


# ─── DNS ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "dns")
async def cb_dns(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dns_waiting_url)
    await state.update_data(mode="dns")
    await _delete_and_send(cb, "🌐 <b>DNS / IP анализ</b>\n\nОтправьте домен или IP:", reply_markup=cancel_kb())
    await cb.answer()


# ─── DDoS check ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ddos_check")
async def cb_ddos(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dns_waiting_url)
    await state.update_data(mode="ddos")
    await _delete_and_send(cb, "🛡 <b>Проверка DDoS-защиты</b>\n\nОтправьте домен или IP:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.dns_waiting_url)
async def handle_dns_or_ddos_url(message: Message, state: FSMContext):
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
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=back_to_menu_kb())


# ─── History ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery):
    uid = cb.from_user.id
    history = storage.get_history(uid)
    if not history:
        await cb.answer("📋 История пуста", show_alert=True)
        return

    lines = ["📋 <b>История проверок</b>\n"]
    for h in history:
        domain = (h["url"] or "")[:40]
        score = h.get("score", "—")
        date = h.get("date", "")
        rid = h.get("report_id")
        if rid:
            lines.append(f"• <a href='{_report_url(rid)}'>{domain}</a> — {score}pts {date}")
        else:
            lines.append(f"• {domain} — {score}pts {date}")

    await _delete_and_send(
        cb,
        "\n".join(lines),
        reply_markup=back_to_menu_kb(),
    )
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
        "⚡ Нагрузочный тест до 2000 RPS\n"
        "💥 Flood-режим до 3000 RPS\n"
        "🐢 Slowloris / RUDY / Cache Bust атаки\n"
        "🤖 Авто-выбор лучшего метода атаки\n"
        "🔄 Ротация User-Agent и сессий\n"
        "🛡 Обнаружение Cloudflare / Akamai\n"
        "📊 Подробные отчёты с графиками\n\n"
        "Выберите план:"
    )
    await _delete_and_send(cb, text, reply_markup=subscription_menu_kb(prices, is_pro, expires))
    await cb.answer()


# ─── LITE stress test ─────────────────────────────────────────────────────────

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
        "⚡ <b>LITE-тест</b>\n\nЗапускается на серверах платформы.\nВыберите профиль:",
        reply_markup=stress_lite_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("lite:"))
async def cb_lite_run(cb: CallbackQuery, state: FSMContext):
    _, duration, rps = cb.data.split(":")
    await state.set_state(UserStates.stress_waiting_url)
    await state.update_data(mode="lite", duration=int(duration), rps=int(rps))
    await _delete_and_send(cb, "🌐 Отправьте URL для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_waiting_url)
async def handle_lite_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    url = message.text.strip()

    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    if not storage.can_analyze(uid):
        await message.answer("🚫 Лимит тестов исчерпан.")
        return

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)

    duration = data.get("duration", 60)
    max_rps = data.get("rps", 100)

    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "lite",
        "duration": duration,
        "intensity": "low",
        "max_rps": max_rps,
        "method_type": "http_flood",
    })

    await message.answer(
        f"⚡ <b>LITE-тест запущен</b>\n\n"
        f"🌐 <code>{url_or_err}</code>\n"
        f"⏱ {duration} сек · {max_rps} RPS\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── PRO stress test ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress_pro")
async def cb_stress_pro(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO-подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "⚡ <b>PRO-тест</b>\n\nВыполняется на серверах платформы.\nВыберите мощность:",
        reply_markup=stress_pro_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro:"))
async def cb_pro_intensity(cb: CallbackQuery):
    parts = cb.data.split(":")
    duration, intensity = parts[1], parts[2]
    await _delete_and_send(
        cb,
        f"⚡ <b>PRO · {intensity.upper()}</b>\n\nВыберите метод атаки:\n\n"
        "🤖 <b>Авто</b> — бот проанализирует цель и выберет лучший метод\n"
        "💥 <b>HTTP Flood</b> — массовые GET/POST/HEAD запросы\n"
        "🐢 <b>Slowloris</b> — держит соединения открытыми, истощает пул сервера\n"
        "🪛 <b>RUDY</b> — медленный POST, блокирует воркеры сервера\n"
        "🚫 <b>Cache Bust</b> — уникальные URL, обходит Cloudflare/CDN-кэш",
        reply_markup=pro_method_kb(int(duration), intensity),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro_method:"))
async def cb_pro_method(cb: CallbackQuery, state: FSMContext):
    _, duration, intensity, method_type = cb.data.split(":")
    await state.set_state(UserStates.stress_pro_waiting_url)
    await state.update_data(mode="pro", duration=int(duration), intensity=intensity, method_type=method_type)
    await _delete_and_send(cb, "🌐 Отправьте URL для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_pro_waiting_url)
async def handle_pro_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    url = message.text.strip()

    if not storage.is_pro(uid):
        await message.answer("🚫 Требуется PRO-подписка.")
        return

    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 120)
    intensity = data.get("intensity", "medium")
    method_type = data.get("method_type", "auto")

    method_label = {
        "auto": "🤖 Авто",
        "http_flood": "💥 HTTP Flood",
        "slowloris": "🐢 Slowloris",
        "rudy": "🪛 RUDY",
        "cache_bust": "🚫 Cache Bust",
    }.get(method_type, method_type)

    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "pro",
        "duration": duration,
        "intensity": intensity,
        "method_type": method_type,
    })

    auto_note = "\n🔍 <i>Бот анализирует цель и выберет оптимальный метод</i>" if method_type == "auto" else ""

    await message.answer(
        f"⚡ <b>PRO-тест запущен</b>\n\n"
        f"🌐 <code>{url_or_err}</code>\n"
        f"📊 Профиль: <b>{intensity.upper()}</b> · {duration} сек\n"
        f"🔫 Метод: <b>{method_label}</b>{auto_note}\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── FLOOD stress test ────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress_flood")
async def cb_stress_flood(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO-подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "💥 <b>Flood-тест</b>\n\n"
        "Агрессивный режим: максимальный поток запросов,\n"
        "ротация заголовков и сессий, мульти-метод.\n\n"
        "Выберите мощность:",
        reply_markup=stress_flood_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("flood:"))
async def cb_flood_intensity(cb: CallbackQuery):
    parts = cb.data.split(":")
    duration, intensity = parts[1], parts[2]
    await _delete_and_send(
        cb,
        f"💥 <b>Flood · {intensity.upper()}</b>\n\nВыберите метод атаки:\n\n"
        "🤖 <b>Авто</b> — бот проанализирует цель и выберет лучший метод\n"
        "💥 <b>HTTP Flood</b> — массовые GET/POST/HEAD/OPTIONS запросы\n"
        "🐢 <b>Slowloris</b> — держит соединения открытыми\n"
        "🪛 <b>RUDY</b> — блокирует воркеры медленным POST-телом\n"
        "🚫 <b>Cache Bust</b> — уникальные URL, обходит Cloudflare/CDN",
        reply_markup=flood_method_kb(int(duration), intensity),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("flood_method:"))
async def cb_flood_method(cb: CallbackQuery, state: FSMContext):
    _, duration, intensity, method_type = cb.data.split(":")
    await state.set_state(UserStates.stress_flood_waiting_url)
    await state.update_data(mode="flood", duration=int(duration), intensity=intensity, method_type=method_type)
    await _delete_and_send(cb, "🌐 Отправьте URL для теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_flood_waiting_url)
async def handle_flood_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    url = message.text.strip()

    if not storage.is_pro(uid):
        await message.answer("🚫 Требуется PRO-подписка.")
        return

    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 60)
    intensity = data.get("intensity", "medium")
    method_type = data.get("method_type", "auto")

    method_label = {
        "auto": "🤖 Авто",
        "http_flood": "💥 HTTP Flood",
        "slowloris": "🐢 Slowloris",
        "rudy": "🪛 RUDY",
        "cache_bust": "🚫 Cache Bust",
    }.get(method_type, method_type)

    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "flood",
        "duration": duration,
        "intensity": intensity,
        "method_type": method_type,
    })

    auto_note = "\n🔍 <i>Бот анализирует цель и выберет лучший метод</i>" if method_type == "auto" else ""

    await message.answer(
        f"💥 <b>Flood-тест запущен</b>\n\n"
        f"🌐 <code>{url_or_err}</code>\n"
        f"📊 Профиль: <b>{intensity.upper()}</b> · {duration} сек\n"
        f"🔫 Метод: <b>{method_label}</b>{auto_note}\n\n"
        "📩 Отчёт придёт сюда по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )
