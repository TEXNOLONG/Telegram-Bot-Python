import logging
import os
import uuid
from io import BytesIO

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import ADMIN_ID, DOMAIN, SUBSCRIPTION_PLANS
from bot.keyboards import (
    back_to_menu_kb, cancel_kb, main_menu_kb,
    register_kb, stress_lite_kb, stress_pro_kb, subscription_menu_kb,
)
from bot.storage import storage
from bot.utils.url_validator import validate_target_url
from bot.utils.site_analyzer import analyze_site
from bot.utils.ssl_checker import check_ssl, format_ssl_report
from bot.utils.dns_checker import dns_lookup, check_ports, format_dns_report
from bot.utils.ddos_checker import check_ddos_protection, format_ddos_report
from bot.utils.script_generator import generate_lite_script
from bot.utils.traffic_worker import enqueue_task
from bot.db import get_session
from bot.models import Report, Setting

logger = logging.getLogger(__name__)
router = Router()


class UserStates(StatesGroup):
    waiting_for_url = State()
    ssl_waiting_url = State()
    dns_waiting_url = State()
    stress_waiting_url = State()
    stress_pro_waiting_url = State()


def _report_url(report_id: str) -> str:
    return f"https://{DOMAIN}/report/{report_id}"


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
    greeting = (
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"⚡ <b>LoadTest Pro</b> — профессиональный инструмент нагрузочного тестирования.\n\n"
    )

    if not is_registered:
        greeting += (
            "📋 <b>Для начала пройдите веб-регистрацию</b> — нажмите кнопку ниже.\n"
            "Это займёт одну секунду."
        )
        kb = register_kb(uid, DOMAIN)
    else:
        tier = "PRO 💎" if is_pro else "LITE 🔧"
        greeting += f"Ваш тариф: <b>{tier}</b>\n\nВыберите действие:"
        kb = main_menu_kb(has_sub=is_pro, is_registered=True)

    if banner:
        await message.answer_photo(photo=banner, caption=greeting, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await message.answer(greeting, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.message(Command("register"))
async def cmd_register(message: Message):
    uid = message.from_user.id
    if storage.is_web_registered(uid):
        await message.answer("✅ Вы уже зарегистрированы. Используйте /start")
        return
    await message.answer(
        "🔗 Нажмите кнопку ниже для регистрации:",
        reply_markup=register_kb(uid, DOMAIN)
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    await show_status(message.from_user.id, message)


@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery):
    await cb.answer()
    await show_status(cb.from_user.id, cb.message)


async def show_status(uid: int, target):
    is_pro = storage.is_pro(uid)
    is_reg = storage.is_web_registered(uid)
    user = storage.get_user(uid)

    tier_line = "💎 <b>PRO</b>" if is_pro else "🔧 <b>LITE</b>"
    expires = storage.sub_expires_str(uid)
    exp_line = f"\n📅 Подписка до: <b>{expires}</b>" if expires and is_pro else ""
    reg_line = "✅ Зарегистрирован" if is_reg else "❌ Не зарегистрирован"
    free_left = storage.free_left(uid) if not is_pro else "∞"
    analyses = (user or {}).get("total_analyses", 0)

    text = (
        f"👤 <b>Ваш статус</b>\n\n"
        f"🏷 Тариф: {tier_line}{exp_line}\n"
        f"🌐 Регистрация: {reg_line}\n"
        f"🆓 Бесплатных сегодня: <b>{free_left}</b>\n"
        f"📊 Всего проверок: <b>{analyses}</b>"
    )
    await target.answer(text, parse_mode=ParseMode.HTML,
                        reply_markup=back_to_menu_kb(is_pro, is_reg))


# ─── Menu navigation ──────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu", "main_menu"}))
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    is_reg = storage.is_web_registered(uid)
    await cb.message.answer("📋 Главное меню:", reply_markup=main_menu_kb(is_pro, is_reg))
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
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
        await cb.answer(
            f"⛔ Лимит исчерпан ({limit} бесплатных/день). Подключите PRO!",
            show_alert=True
        )
        return
    await state.set_state(UserStates.waiting_for_url)
    await cb.message.answer(
        "🔍 Отправьте URL сайта для анализа:\n\n<code>https://example.com</code>",
        parse_mode=ParseMode.HTML, reply_markup=cancel_kb()
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
        await message.answer("⛔ Лимит анализов на сегодня исчерпан. Подключите PRO!")
        return

    msg = await message.answer("🔍 Анализирую сайт...")
    try:
        result = await analyze_site(url_or_err)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка анализа: {e}")
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

    rurl = _report_url(report_id)
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Открыть отчёт", url=rurl)],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")],
    ])

    await msg.edit_text(
        f"✅ <b>Анализ завершён</b>\n\n"
        f"🌐 <code>{url_or_err}</code>\n"
        f"⭐ Балл: <b>{score}/100</b>\n\n"
        f"🔗 <a href='{rurl}'>Открыть полный отчёт</a>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=False,
    )


# ─── SSL ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ssl")
async def cb_ssl(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ssl_waiting_url)
    await cb.message.answer("🔐 Отправьте домен для проверки SSL:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.ssl_waiting_url)
async def handle_ssl_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    mode = data.get("mode", "ssl")
    host = message.text.strip()
    msg = await message.answer("🔐 Проверяю...")
    try:
        if mode == "ddos":
            result = await check_ddos_protection(host)
            text = format_ddos_report(result)
        else:
            result = await check_ssl(host)
            text = format_ssl_report(host, result)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=back_to_menu_kb())


# ─── DNS ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "dns")
async def cb_dns(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dns_waiting_url)
    await cb.message.answer("🌐 Отправьте домен или IP:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.dns_waiting_url)
async def handle_dns_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    mode = data.get("mode", "dns")
    host = message.text.strip()
    msg = await message.answer("🌐 Проверяю...")
    try:
        if mode == "ddos":
            result = await check_ddos_protection(host)
            text = format_ddos_report(result)
        else:
            dns_data = await dns_lookup(host)
            ports_data = await check_ports(host, [80, 443, 8080, 8443, 22, 21])
            text = format_dns_report(host, dns_data, ports_data)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=back_to_menu_kb())


# ─── DDoS check ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ddos_check")
async def cb_ddos(cb: CallbackQuery, state: FSMContext):
    from bot.utils.ddos_checker import check_ddos_protection
    uid = cb.from_user.id
    await state.set_state(UserStates.dns_waiting_url)
    await state.update_data(mode="ddos")
    await cb.message.answer("🛡️ Отправьте домен для проверки DDoS-защиты:", reply_markup=cancel_kb())
    await cb.answer()


# ─── History ─────────────────────────────────────────────────────────────────

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
            lines.append(f"• <a href='{_report_url(rid)}'>{domain}</a> — {score}pts <i>{date}</i>")
        else:
            lines.append(f"• {domain} — {score}pts <i>{date}</i>")

    await cb.message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
        disable_web_page_preview=True,
    )
    await cb.answer()


# ─── Subscription ────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"sub", "buy"}))
async def cb_sub(cb: CallbackQuery):
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    expires = storage.sub_expires_str(uid)
    prices = storage.get_prices()

    status_line = ""
    if is_pro and expires:
        status_line = f"✅ <b>Ваша подписка PRO активна</b> до <b>{expires}</b>\n\n"

    text = (
        f"{status_line}"
        f"💎 <b>Подписка PRO</b>\n\n"
        f"PRO включает:\n"
        f"• Нагрузочный тест до 2000 RPS\n"
        f"• Эмуляция реального трафика\n"
        f"• Обход кэш-слоёв (ротация сессий)\n"
        f"• Обнаружение Cloudflare / Akamai\n"
        f"• Без лимита по времени\n"
        f"• Отчёты с графиками\n\n"
        f"Выберите план:"
    )
    await cb.message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=subscription_menu_kb(prices, is_pro, expires)
    )
    await cb.answer()


# ─── LITE stress test ────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress_lite")
async def cb_stress_lite(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_web_registered(uid):
        await cb.answer("Сначала пройдите веб-регистрацию (/register)", show_alert=True)
        return
    if not storage.can_analyze(uid):
        await cb.answer("Лимит тестов исчерпан", show_alert=True)
        return
    await cb.message.answer(
        "🔧 <b>LITE нагрузочный тест</b>\n\n"
        "Скрипт запускается локально с <b>вашего IP</b>.\n"
        "Ограничения: 100 RPS · 60 секунд · только HTTP GET.\n\n"
        "Выберите профиль:",
        parse_mode=ParseMode.HTML,
        reply_markup=stress_lite_kb()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("lite:"))
async def cb_lite_run(cb: CallbackQuery, state: FSMContext):
    _, duration, rps = cb.data.split(":")
    await state.set_state(UserStates.stress_waiting_url)
    await state.update_data(mode="lite", duration=int(duration), rps=int(rps))
    await cb.message.answer("🌐 Отправьте URL для нагрузочного теста:", reply_markup=cancel_kb())
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

    duration = data.get("duration", 60)
    max_rps = data.get("rps", 100)

    report_token = str(uuid.uuid4())
    with get_session() as session:
        key = f"lite_token_{uid}"
        s = session.query(Setting).filter_by(key=key).first()
        if s:
            s.value = report_token
        else:
            session.add(Setting(key=key, value=report_token))

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)

    script = generate_lite_script(url_or_err, uid, report_token, max_rps, duration)
    file_bytes = script.encode("utf-8")
    filename = f"lite_test_{uid}.py"

    await message.answer_document(
        document=BufferedInputFile(file_bytes, filename=filename),
        caption=(
            f"⚡ <b>LITE-скрипт готов</b>\n\n"
            f"🌐 Цель: <code>{url_or_err}</code>\n"
            f"⏱ Длительность: <b>{duration} сек</b> | RPS: <b>{max_rps}</b>\n\n"
            f"<b>Запуск:</b>\n"
            f"<code>pip install aiohttp</code>\n"
            f"<code>python3 {filename}</code>\n\n"
            f"После завершения отчёт придёт в этот чат автоматически 📊"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── PRO stress test ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress_pro")
async def cb_stress_pro(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("PRO-подписка требуется!", show_alert=True)
        return
    await cb.message.answer(
        "⚡ <b>PRO нагрузочный тест</b>\n\n"
        "Тест выполняется на серверах платформы.\n"
        "Методы: GET · POST · HEAD\n"
        "Активна ротация User-Agent и сессионных кук.\n\n"
        "Выберите профиль интенсивности:",
        parse_mode=ParseMode.HTML,
        reply_markup=stress_pro_kb()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro:"))
async def cb_pro_run(cb: CallbackQuery, state: FSMContext):
    _, duration, intensity = cb.data.split(":")
    await state.set_state(UserStates.stress_pro_waiting_url)
    await state.update_data(mode="pro", duration=int(duration), intensity=intensity)
    await cb.message.answer("🌐 Отправьте URL для PRO нагрузочного теста:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_pro_waiting_url)
async def handle_pro_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    url = message.text.strip()

    if not storage.is_pro(uid):
        await message.answer("⛔ Требуется PRO-подписка.")
        return

    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return

    task_id = enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "pro",
        "duration": data.get("duration", 120),
        "intensity": data.get("intensity", "medium"),
    })

    await message.answer(
        f"⚡ <b>PRO тест запущен!</b>\n\n"
        f"🌐 Цель: <code>{url_or_err}</code>\n"
        f"📊 Профиль: <b>{data.get('intensity', 'medium').upper()}</b>\n"
        f"⏱ Длительность: <b>{data.get('duration', 120)} сек</b>\n\n"
        f"Когда тест завершится — отчёт придёт сюда 🔔",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )
