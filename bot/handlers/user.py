import asyncio
import logging
import socket
import uuid
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import DOMAIN, ADMIN_ID
from bot.keyboards import (
    back_to_menu_kb, cancel_kb, main_menu_kb,
    register_kb, stress_lite_kb, stress_pro_kb, stress_flood_kb,
    subscription_menu_kb, my_tests_kb, referral_kb,
    schedule_mode_kb, schedule_delay_kb,
    intensity_kb, flood_method_kb, test_url_prompt_kb, stop_test_kb,
)
from bot.storage import storage
from bot.utils.url_validator import validate_target_url
from bot.utils.site_analyzer import analyze_site, format_report
from bot.utils.traffic_worker import enqueue_task, cancel_task
from bot.db import get_session
from bot.models import Report

logger = logging.getLogger(__name__)
router = Router()


class UserStates(StatesGroup):
    waiting_for_url = State()
    stress_waiting_url = State()
    stress_pro_waiting_url = State()
    stress_flood_waiting_url = State()
    ip_check_waiting = State()
    schedule_waiting_url = State()


def _report_url(report_id: str) -> str:
    return f"https://{DOMAIN}/report/{report_id}"


def _report_kb(report_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть отчёт", url=_report_url(report_id))],
        [InlineKeyboardButton(text="В меню", callback_data="menu_back")],
    ])


async def _delete_and_send(cb: CallbackQuery, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def _resolve_ip(host: str) -> str | None:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, socket.gethostbyname, host)
    except Exception:
        return None


# ─── /start ───────────────────────────────────────────────────────────────────

async def _notify_admin_blocked(bot_obj, uid: int, url: str, reason: str,
                                user_ip: str, action: str, attempts: int):
    """Send a security alert to the admin when someone tries a blacklisted domain."""
    try:
        action_text = {"banned": "🚫 ЗАБАНЕН", "warned": "⚠️ Предупреждён",
                       "already_banned": "🚫 Уже заблокирован"}.get(action, action)
        await bot_obj.send_message(
            ADMIN_ID,
            f"🛡 <b>Попытка атаки запрещённого домена!</b>\n\n"
            f"👤 user_id: <code>{uid}</code>\n"
            f"🌐 IP: <code>{user_ip}</code>\n"
            f"🔗 URL: <code>{escape(url)}</code>\n"
            f"📛 Причина: {escape(reason)}\n"
            f"🔢 Попыток: <b>{attempts}</b>\n"
            f"⚡ Действие: <b>{action_text}</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Failed to notify admin about blocked attempt: %s", e)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id

    if storage.is_banned(uid):
        await message.answer("Заблокировано.")
        return

    storage.upsert_user(uid, message.from_user.first_name, message.from_user.username)

    # Handle referral deep link: /start ref_XXXXXXXX
    args = message.text.split() if message.text else []
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1][4:].upper()
        referrer = storage.get_user_by_referral_code(ref_code)
        if referrer and referrer["id"] != uid:
            applied = storage.apply_referral(uid, referrer["id"])
            if applied:
                try:
                    from aiogram import Bot
                    bot_obj = message.bot
                    await bot_obj.send_message(
                        referrer["id"],
                        f"🎉 <b>Новый реферал!</b>\n\n"
                        f"По вашей ссылке зарегистрировался новый пользователь.\n"
                        f"Вам начислено <b>+2 бонусных теста</b>! 🎁",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
    is_registered = storage.is_web_registered(uid)
    is_pro = storage.is_pro(uid)
    banner = storage.get_banner()

    if not is_registered:
        text = (
            f"<b>LoadTest Pro</b>\n\n"
            "Профессиональный инструмент анализа и нагрузочного тестирования.\n\n"
            "Для начала работы пройдите регистрацию:"
        )
        kb = register_kb(uid, DOMAIN)
    else:
        tier = "PRO" if is_pro else "Lite"
        expires = storage.sub_expires_str(uid)
        exp_line = f"\nПодписка до: <b>{expires}</b>" if expires and is_pro else ""
        free_left = storage.free_left(uid)
        text = (
            f"<b>LoadTest Pro</b>\n\n"
            f"Тариф: <b>{tier}</b>{exp_line}"
            + (f"\nБесплатных сегодня: <b>{free_left}</b>" if not is_pro else "")
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
    free_line = f"\nОсталось бесплатных: <b>{free_left}</b>" if free_left is not None else ""
    await _delete_and_send(
        cb,
        f"<b>LoadTest Pro</b>{free_line}",
        reply_markup=main_menu_kb(is_pro, is_reg),
    )
    await cb.answer()


@router.callback_query(F.data == "need_reg")
async def cb_need_reg(cb: CallbackQuery):
    uid = cb.from_user.id
    await _delete_and_send(
        cb,
        "<b>Требуется регистрация</b>\n\nНажмите кнопку:",
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
    free_left = storage.free_left(uid) if not is_pro else "безлимит"
    analyses = (user or {}).get("total_analyses", 0)
    is_reg = storage.is_web_registered(uid)

    tier = "PRO" if is_pro else "Lite"
    exp_line = f"\nДо: <b>{expires}</b>" if expires and is_pro else ""
    reg_line = "да" if is_reg else "нет"

    await _delete_and_send(
        cb,
        f"<b>Статус</b>\n\n"
        f"Тариф: <b>{tier}</b>{exp_line}\n"
        f"Регистрация: {reg_line}\n"
        f"Доступно сегодня: <b>{free_left}</b>\n"
        f"Всего проверок: <b>{analyses}</b>",
        reply_markup=back_to_menu_kb(),
    )
    await cb.answer()


# ─── Site analysis + Vulnerability scan ───────────────────────────────────────

@router.callback_query(F.data == "analyze")
async def cb_analyze(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if not storage.can_analyze(uid):
        limit = storage.get_free_limit()
        await cb.answer(f"Лимит {limit}/день исчерпан. Нужна PRO подписка.", show_alert=True)
        return
    await state.set_state(UserStates.waiting_for_url)
    await _delete_and_send(
        cb,
        "<b>Анализ сайта</b>\n\n"
        "Проверяю: SEO, SSL, безопасность, производительность,\n"
        "DNS, технологии, уязвимости, доступность, контент.\n\n"
        "Отправьте URL:",
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
        await message.answer(f"Ошибка: {url_or_err}", reply_markup=cancel_kb())
        return

    if not storage.can_analyze(uid):
        await message.answer("Лимит исчерпан.")
        return

    msg = await message.answer(
        "<b>Анализирую...</b>\n<i>SEO / SSL / Безопасность / Уязвимости / DNS / Технологии</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        result = await analyze_site(url_or_err)
    except Exception as e:
        try:
            await msg.edit_text(f"Ошибка анализа: {escape(str(e)[:200])}")
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
    vuln_count = len(result.get("vulnerabilities", []))

    try:
        storage.add_history(uid, url_or_err, score, report_id)
    except Exception:
        pass

    try:
        short_report = format_report(result)
        if len(short_report) > 3500:
            short_report = short_report[:3500] + "\n\n<i>...подробнее в отчёте</i>"
        await msg.edit_text(
            short_report,
            parse_mode=ParseMode.HTML,
            reply_markup=_report_kb(report_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("edit_text failed: %s", e)
        try:
            vuln_line = f"\nУязвимостей: <b>{vuln_count}</b>" if vuln_count else ""
            await msg.edit_text(
                f"<b>Анализ завершён</b>\n\n"
                f"URL: <code>{escape(url_or_err)}</code>\n"
                f"Оценка: <b>{score}/100</b>{vuln_line}\n\n"
                f"Полный отчёт по ссылке:",
                parse_mode=ParseMode.HTML,
                reply_markup=_report_kb(report_id),
            )
        except Exception:
            pass


# ─── Subscription ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"sub", "buy"}))
async def cb_sub(cb: CallbackQuery):
    uid = cb.from_user.id
    is_pro = storage.is_pro(uid)
    expires = storage.sub_expires_str(uid)
    prices = storage.get_prices()

    status = f"Подписка активна до <b>{expires}</b>\n\n" if (is_pro and expires) else ""
    text = (
        f"{status}<b>PRO подписка</b>\n\n"
        "— Нагрузка до 2000+ RPS\n"
        "— Flood режим (до 5000 RPS)\n"
        "— Авто-выбор метода атаки\n"
        "— Slowloris / RUDY / Cache Bust\n"
        "— Обход Cloudflare / CDN / WAF\n"
        "— Анализ уязвимостей без лимита\n"
        "— Подробные отчёты с графиками\n\n"
        "Выберите план:"
    )
    await _delete_and_send(cb, text, reply_markup=subscription_menu_kb(prices, is_pro, expires))
    await cb.answer()


# ─── LITE DDoS ─── тест по IP цели ───────────────────────────────────────────

@router.callback_query(F.data == "stress_lite")
async def cb_stress_lite(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_web_registered(uid):
        await cb.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    if not storage.can_analyze(uid):
        await cb.answer("Лимит исчерпан.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "<b>DDoS Lite</b>\n\n"
        "Бот определит IP-адрес цели и запустит тест\n"
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
    await _delete_and_send(cb, "Отправьте URL или домен:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(UserStates.stress_waiting_url)
async def handle_lite_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id

    raw_url = message.text.strip()
    ok, url_or_err = validate_target_url(raw_url)
    if not ok:
        if "заблокирован" in url_or_err or "суффикс" in url_or_err:
            user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
            sec = storage.track_blocked_attempt(uid, raw_url, url_or_err, user_ip)
            await _notify_admin_blocked(message.bot, uid, raw_url, url_or_err,
                                        user_ip, sec["action"], sec["attempts"])
            if sec["action"] == "banned":
                await message.answer("🚫 Вы заблокированы за попытку атаки защищённого ресурса.")
                return
        await message.answer(f"Ошибка: {url_or_err}", reply_markup=cancel_kb())
        return

    if not storage.can_analyze(uid):
        await message.answer("Лимит исчерпан.")
        return

    wait_msg = await message.answer("Определяю IP-адрес цели...")
    parsed = urlparse(url_or_err)
    ip = await _resolve_ip(parsed.hostname or url_or_err)

    if not ip:
        await wait_msg.edit_text("Не удалось определить IP. Проверьте домен.", reply_markup=cancel_kb())
        return

    scheme = parsed.scheme or "http"
    ip_url = f"{scheme}://{ip}/"

    if not storage.is_pro(uid):
        storage.use_free_analysis(uid)

    duration = data.get("duration", 60)
    max_rps = data.get("rps", 100)

    user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
    enqueue_task(uid, "load_test", {
        "target_url": ip_url,
        "original_url": url_or_err,
        "mode": "lite",
        "duration": duration,
        "intensity": "low",
        "max_rps": max_rps,
        "method_type": "http_flood",
        "user_ip": user_ip,
    })

    await wait_msg.edit_text(
        f"<b>DDoS Lite запущен</b>\n\n"
        f"Цель: <code>{escape(url_or_err)}</code>\n"
        f"IP: <code>{ip}</code> (прямой, без CDN)\n"
        f"Длительность: {duration} сек / {max_rps} RPS\n\n"
        "Отчёт придёт по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── PRO DDoS ── авто-режим ───────────────────────────────────────────────────

@router.callback_query(F.data == "stress_pro")
async def cb_stress_pro(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "<b>DDoS PRO</b>\n\n"
        "Бот автоматически:\n"
        "— Резолвит IP и тестирует напрямую\n"
        "— Выбирает лучший метод атаки\n"
        "— Выставляет максимальную интенсивность\n\n"
        "Выберите длительность:",
        reply_markup=stress_pro_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro:"))
async def cb_pro_duration(cb: CallbackQuery, state: FSMContext):
    duration = int(cb.data.split(":")[1])
    await state.update_data(duration=duration)
    await _delete_and_send(
        cb,
        f"<b>DDoS PRO — {duration} сек</b>\n\nВыберите интенсивность:",
        reply_markup=intensity_kb(duration),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pro_int:"))
async def cb_pro_intensity(cb: CallbackQuery, state: FSMContext):
    _, duration_s, intensity = cb.data.split(":")
    duration = int(duration_s)
    await state.set_state(UserStates.stress_pro_waiting_url)
    await state.update_data(duration=duration, intensity=intensity, use_proxies=False)
    int_labels = {"low": "🟢 Низкий", "medium": "🟡 Средний", "high": "🟠 Высокий", "ultra": "🔴 Ультра"}
    await _delete_and_send(
        cb,
        f"<b>DDoS PRO</b>\n\n"
        f"⏱ Длительность: <b>{duration} сек</b>\n"
        f"📊 Интенсивность: <b>{int_labels.get(intensity, intensity)}</b>\n\n"
        "Отправьте URL или домен цели.\n"
        "Прокси можно включить кнопкой ниже:",
        reply_markup=test_url_prompt_kb("pro", False),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("toggle_proxy:"))
async def cb_toggle_proxy(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    mode = parts[1]
    current = bool(int(parts[2]))
    new_val = not current
    await state.update_data(use_proxies=new_val)
    data = await state.get_data()
    duration = data.get("duration", 60)
    intensity = data.get("intensity", "high")
    method = data.get("method_type", "auto")
    int_labels = {"low": "🟢 Низкий", "medium": "🟡 Средний", "high": "🟠 Высокий", "ultra": "🔴 Ультра"}
    method_labels = {
        "auto": "Авто", "mixed": "Mixed", "cache_bust": "Cache Bust",
        "http_flood": "HTTP Flood", "slowloris": "Slowloris", "rudy": "RUDY",
        "range_amplify": "Range Amplify", "dns_flood": "DNS Flood", "websocket": "WebSocket",
    }
    if mode == "pro":
        text = (
            f"<b>DDoS PRO</b>\n\n"
            f"⏱ Длительность: <b>{duration} сек</b>\n"
            f"📊 Интенсивность: <b>{int_labels.get(intensity, intensity)}</b>\n\n"
            "Отправьте URL или домен цели.\n"
            "Прокси можно включить кнопкой ниже:"
        )
    else:
        text = (
            f"<b>DDoS Flood</b>\n\n"
            f"⏱ Длительность: <b>{duration} сек</b>\n"
            f"🔫 Метод: <b>{method_labels.get(method, method)}</b>\n\n"
            "Отправьте URL или домен цели.\n"
            "Прокси можно включить кнопкой ниже:"
        )
    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=test_url_prompt_kb(mode, new_val),
        )
    except Exception:
        pass
    status = "включены ✅" if new_val else "выключены ❌"
    await cb.answer(f"Прокси {status}", show_alert=False)


@router.callback_query(F.data.startswith("stop_task:"))
async def cb_stop_task(cb: CallbackQuery):
    task_id = cb.data.split(":", 1)[1]
    found = cancel_task(task_id)
    if found:
        await cb.answer("⏹ Тест остановлен", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    else:
        await cb.answer("Тест уже завершён или не найден.", show_alert=True)


@router.message(UserStates.stress_pro_waiting_url)
async def handle_pro_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id

    if not storage.is_pro(uid):
        await message.answer("Требуется PRO подписка.")
        return

    raw_url = message.text.strip()
    ok, url_or_err = validate_target_url(raw_url)
    if not ok:
        if "заблокирован" in url_or_err or "суффикс" in url_or_err:
            user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
            sec = storage.track_blocked_attempt(uid, raw_url, url_or_err, user_ip)
            await _notify_admin_blocked(message.bot, uid, raw_url, url_or_err,
                                        user_ip, sec["action"], sec["attempts"])
            if sec["action"] == "banned":
                await message.answer("🚫 Вы заблокированы за попытку атаки защищённого ресурса.")
                return
        await message.answer(f"Ошибка: {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 120)
    intensity = data.get("intensity", "high")
    use_proxies = data.get("use_proxies", False)

    int_labels = {"low": "🟢 Низкий", "medium": "🟡 Средний", "high": "🟠 Высокий", "ultra": "🔴 Ультра"}
    proxy_label = "Вкл ✅" if use_proxies else "Выкл ❌"

    user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
    progress_msg = await message.answer(
        f"⚡ <b>DDoS PRO запущен</b>\n\n"
        f"🎯 <code>{escape(url_or_err)}</code>\n"
        f"⏱ Длительность: <b>{duration} сек</b>\n"
        f"📊 Интенсивность: <b>{int_labels.get(intensity, intensity)}</b>\n"
        f"🌐 Прокси: {proxy_label}\n\n"
        "Прогресс обновляется каждые 10 секунд...",
        parse_mode=ParseMode.HTML,
    )
    task_id = enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "pro",
        "duration": duration,
        "intensity": intensity,
        "method_type": "auto",
        "use_proxies": use_proxies,
        "user_ip": user_ip,
        "progress_chat_id": message.chat.id,
        "progress_msg_id": progress_msg.message_id,
    })
    await message.answer(
        f"✅ <b>DDoS PRO в очереди!</b>\n\n"
        f"🎯 <code>{escape(url_or_err)}</code>\n"
        f"Отчёт и статистика придут по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── FLOOD DDoS ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress_flood")
async def cb_stress_flood(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Требуется PRO подписка.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "<b>💥 DDoS Flood</b>\n\n"
        "Агрессивный режим с максимальным потоком запросов.\n\n"
        "Выберите длительность:",
        reply_markup=stress_flood_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("flood:"))
async def cb_flood_duration(cb: CallbackQuery, state: FSMContext):
    duration = int(cb.data.split(":")[1])
    await state.update_data(duration=duration)
    await _delete_and_send(
        cb,
        f"<b>💥 DDoS Flood — {duration} сек</b>\n\nВыберите метод атаки:",
        reply_markup=flood_method_kb(duration),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("flood_method:"))
async def cb_flood_method(cb: CallbackQuery, state: FSMContext):
    _, duration_s, method = cb.data.split(":", 2)
    duration = int(duration_s)
    await state.set_state(UserStates.stress_flood_waiting_url)
    await state.update_data(duration=duration, method_type=method, use_proxies=False)
    method_labels = {
        "auto": "Авто", "mixed": "Mixed", "cache_bust": "Cache Bust",
        "http_flood": "HTTP Flood", "slowloris": "Slowloris", "rudy": "RUDY",
        "range_amplify": "Range Amplify", "dns_flood": "DNS Flood", "websocket": "WebSocket",
    }
    await _delete_and_send(
        cb,
        f"<b>💥 DDoS Flood</b>\n\n"
        f"⏱ Длительность: <b>{duration} сек</b>\n"
        f"🔫 Метод: <b>{method_labels.get(method, method)}</b>\n\n"
        "Отправьте URL или домен цели.\n"
        "Прокси можно включить кнопкой ниже:",
        reply_markup=test_url_prompt_kb("flood", False),
    )
    await cb.answer()


# ─── IP Checker ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ip_check")
async def cb_ip_check(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ip_check_waiting)
    await _delete_and_send(
        cb,
        "<b>🌐 Проверка IP / Домена</b>\n\n"
        "Введите домен или IP-адрес.\n"
        "Бот определит:\n"
        "— IP-адрес и геолокацию\n"
        "— Хостинг / ASN / провайдер\n"
        "— Обратный DNS (PTR)\n"
        "— Защита (Cloudflare / CDN)\n"
        "— Открытые порты\n\n"
        "Пример: <code>google.com</code> или <code>1.1.1.1</code>",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.ip_check_waiting)
async def handle_ip_check(message: Message, state: FSMContext):
    await state.clear()
    raw = message.text.strip()

    # Strip protocol if given
    host = raw.replace("https://", "").replace("http://", "").split("/")[0].strip()
    if not host:
        await message.answer("Введите домен или IP.", reply_markup=cancel_kb())
        return

    msg = await message.answer(f"<b>🔍 Анализирую:</b> <code>{escape(host)}</code>...", parse_mode=ParseMode.HTML)

    result = await _full_ip_check(host)
    await msg.edit_text(result, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb(), disable_web_page_preview=True)


async def _full_ip_check(host: str) -> str:
    import aiohttp as _aio

    lines = [f"<b>🌐 IP-проверка:</b> <code>{escape(host)}</code>\n"]

    # Resolve IP
    loop = asyncio.get_event_loop()
    try:
        ip = await loop.run_in_executor(None, socket.gethostbyname, host)
        is_ip_input = host == ip
        lines.append(f"📍 IP-адрес: <code>{ip}</code>")
    except Exception:
        lines.append("❌ Не удалось определить IP-адрес")
        return "\n".join(lines)

    # Reverse DNS
    try:
        ptr = await loop.run_in_executor(None, lambda: socket.gethostbyaddr(ip)[0])
        lines.append(f"🔁 PTR (обратный DNS): <code>{escape(ptr)}</code>")
    except Exception:
        lines.append("🔁 PTR: не настроен")

    # Geo + ASN via ip-api
    try:
        connector = _aio.TCPConnector(ssl=False)
        async with _aio.ClientSession(connector=connector) as session:
            async with session.get(
                f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,isp,org,as,proxy,hosting,mobile",
                timeout=_aio.ClientTimeout(total=8),
            ) as resp:
                geo = await resp.json()

        if geo.get("status") == "success":
            country = geo.get("country", "—")
            cc = geo.get("countryCode", "")
            region = geo.get("regionName", "—")
            city = geo.get("city", "—")
            isp = geo.get("isp", "—")
            org = geo.get("org", "—")
            asn = geo.get("as", "—")
            is_proxy = geo.get("proxy", False)
            is_hosting = geo.get("hosting", False)
            is_mobile = geo.get("mobile", False)

            flag = _country_flag(cc)
            lines.append(f"\n{flag} <b>Геолокация:</b>")
            lines.append(f"  🌍 Страна: <b>{escape(country)}</b>")
            lines.append(f"  🏙 Регион/Город: {escape(region)}, {escape(city)}")
            lines.append(f"  🏢 Провайдер: <code>{escape(isp)}</code>")
            lines.append(f"  🏗 Организация: <code>{escape(org)}</code>")
            lines.append(f"  📡 ASN: <code>{escape(asn)}</code>")

            tags = []
            if is_proxy:
                tags.append("🔒 Proxy/VPN")
            if is_hosting:
                tags.append("🖥 Хостинг/DC")
            if is_mobile:
                tags.append("📱 Мобильный")
            if tags:
                lines.append(f"  🏷 Тип: {' | '.join(tags)}")
    except Exception as e:
        lines.append(f"⚠️ Геолокация недоступна")

    # Cloudflare / CDN detection
    try:
        connector = _aio.TCPConnector(ssl=False)
        async with _aio.ClientSession(connector=connector) as session:
            async with session.get(
                f"http://{host}/",
                timeout=_aio.ClientTimeout(total=6),
                allow_redirects=True,
                ssl=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                hdrs = dict(resp.headers)
                protection = []
                if hdrs.get("CF-RAY") or "cloudflare" in hdrs.get("Server", "").lower():
                    protection.append("🌩 Cloudflare")
                if hdrs.get("X-Akamai-Transformed") or "akamai" in hdrs.get("Via", "").lower():
                    protection.append("🌐 Akamai")
                if hdrs.get("X-Served-By"):
                    protection.append("🚀 Fastly CDN")
                if "ddos-guard" in hdrs.get("Server", "").lower():
                    protection.append("🛡 DDoS-Guard")
                server = hdrs.get("Server", "")
                if server and not protection:
                    protection.append(f"🖥 {escape(server[:30])}")

                lines.append(f"\n🛡 <b>Защита:</b> {'нет / прямой IP' if not protection else ' | '.join(protection)}")
                if resp.status:
                    lines.append(f"📶 HTTP статус: <b>{resp.status}</b>")
    except Exception:
        lines.append("\n🛡 Защита: не удалось проверить (порт 80 закрыт?)")

    # Port scan (common ports)
    lines.append("\n🔓 <b>Открытые порты:</b>")
    open_ports = await _scan_ports(ip, [21, 22, 25, 80, 443, 3306, 3389, 8080, 8443])
    if open_ports:
        port_names = {21: "FTP", 22: "SSH", 25: "SMTP", 80: "HTTP", 443: "HTTPS",
                      3306: "MySQL", 3389: "RDP", 8080: "HTTP-alt", 8443: "HTTPS-alt"}
        port_strs = [f"<code>{p}</code> ({port_names.get(p, '?')})" for p in open_ports]
        lines.append("  " + " | ".join(port_strs))
    else:
        lines.append("  Все проверенные порты закрыты")

    return "\n".join(lines)


async def _scan_ports(ip: str, ports: list) -> list:
    open_ports = []

    async def check(port):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=1.5)
            w.close()
            open_ports.append(port)
        except Exception:
            pass

    await asyncio.gather(*[check(p) for p in ports])
    return sorted(open_ports)


def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2:
        return "🌍"
    try:
        return chr(0x1F1E6 + ord(cc[0]) - ord('A')) + chr(0x1F1E6 + ord(cc[1]) - ord('A'))
    except Exception:
        return "🌍"


@router.message(UserStates.stress_flood_waiting_url)
async def handle_flood_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id

    if not storage.is_pro(uid):
        await message.answer("Требуется PRO подписка.")
        return

    raw_url = message.text.strip()
    ok, url_or_err = validate_target_url(raw_url)
    if not ok:
        if "заблокирован" in url_or_err or "суффикс" in url_or_err:
            user_ip_f = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
            sec = storage.track_blocked_attempt(uid, raw_url, url_or_err, user_ip_f)
            await _notify_admin_blocked(message.bot, uid, raw_url, url_or_err,
                                        user_ip_f, sec["action"], sec["attempts"])
            if sec["action"] == "banned":
                await message.answer("🚫 Вы заблокированы за попытку атаки защищённого ресурса.")
                return
        await message.answer(f"Ошибка: {url_or_err}", reply_markup=cancel_kb())
        return

    duration = data.get("duration", 60)
    method_type = data.get("method_type", "auto")
    use_proxies = data.get("use_proxies", False)
    intensity = "ultra"

    method_labels = {
        "auto": "Авто", "mixed": "Mixed", "cache_bust": "Cache Bust",
        "http_flood": "HTTP Flood", "slowloris": "Slowloris", "rudy": "RUDY",
        "range_amplify": "Range Amplify", "dns_flood": "DNS Flood", "websocket": "WebSocket",
    }
    proxy_label = "Вкл ✅" if use_proxies else "Выкл ❌"

    user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
    progress_msg = await message.answer(
        f"💥 <b>DDoS Flood запущен</b>\n\n"
        f"🎯 <code>{escape(url_or_err)}</code>\n"
        f"⏱ Длительность: <b>{duration} сек</b>\n"
        f"🔫 Метод: <b>{method_labels.get(method_type, method_type)}</b>\n"
        f"🌐 Прокси: {proxy_label}\n\n"
        "Прогресс обновляется каждые 10 секунд...",
        parse_mode=ParseMode.HTML,
    )
    task_id = enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": "flood",
        "duration": duration,
        "intensity": intensity,
        "method_type": method_type,
        "use_proxies": use_proxies,
        "user_ip": user_ip,
        "progress_chat_id": message.chat.id,
        "progress_msg_id": progress_msg.message_id,
    })
    await message.answer(
        f"✅ <b>DDoS Flood в очереди!</b>\n\n"
        f"🎯 <code>{escape(url_or_err)}</code>\n"
        "Отчёт и статистика придут по завершении.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )


# ─── 📋 История тестов ────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_tests")
async def cb_my_tests(cb: CallbackQuery):
    uid = cb.from_user.id
    reports = storage.get_user_reports(uid, limit=8)
    if not reports:
        await _delete_and_send(
            cb,
            "📋 <b>Мои тесты</b>\n\nУ вас ещё нет сохранённых тестов и анализов.\n"
            "Запустите тест — отчёт появится здесь.",
            reply_markup=back_to_menu_kb(),
        )
    else:
        await _delete_and_send(
            cb,
            f"📋 <b>Мои тесты</b>\n\nПоследние {len(reports)} отчётов — нажмите для открытия:",
            reply_markup=my_tests_kb(reports, DOMAIN),
        )
    await cb.answer()


# ─── 🔗 Реферальная система ───────────────────────────────────────────────────

@router.message(Command("ref"))
async def cmd_ref(message: Message):
    uid = message.from_user.id
    await _show_referral(message, uid)


@router.callback_query(F.data == "my_ref")
async def cb_my_ref(cb: CallbackQuery):
    uid = cb.from_user.id
    await _delete_and_send(cb, "⏳")
    await _show_referral(cb.message, uid)
    await cb.answer()


async def _show_referral(msg_obj, uid: int):
    code = storage.get_or_create_referral_code(uid)
    stats = storage.get_referral_stats(uid)

    from bot.config import BOT_TOKEN
    import re
    bot_username_raw = msg_obj.bot.id if hasattr(msg_obj, "bot") else None
    try:
        bot_info = await msg_obj.bot.get_me()
        bot_username = bot_info.username
    except Exception:
        bot_username = "your_bot"

    ref_link = f"https://t.me/{bot_username}?start=ref_{code}"
    text = (
        f"🔗 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей — за каждого получаете <b>+2 бесплатных теста</b>!\n\n"
        f"Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"📊 Приглашено: <b>{stats['count']}</b> человек\n"
        f"🎁 Бонусных тестов: <b>{stats['bonus']}</b>\n\n"
        "Бонусные тесты расходуются автоматически вместо дневного лимита."
    )
    try:
        await msg_obj.edit_text(text, parse_mode=ParseMode.HTML,
                                reply_markup=referral_kb(ref_link),
                                disable_web_page_preview=True)
    except Exception:
        await msg_obj.answer(text, parse_mode=ParseMode.HTML,
                             reply_markup=referral_kb(ref_link),
                             disable_web_page_preview=True)


# ─── ⏱ Планировщик тестов ────────────────────────────────────────────────────

@router.callback_query(F.data == "schedule_test")
async def cb_schedule_test(cb: CallbackQuery):
    uid = cb.from_user.id
    if not storage.is_pro(uid):
        await cb.answer("Планировщик доступен только для PRO.", show_alert=True)
        return
    await _delete_and_send(
        cb,
        "⏱ <b>Планировщик тестов</b>\n\n"
        "Выберите тип теста, который нужно запустить по расписанию:",
        reply_markup=schedule_mode_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sched_mode:"))
async def cb_sched_mode(cb: CallbackQuery):
    mode = cb.data.split(":")[1]
    await _delete_and_send(
        cb,
        f"⏱ <b>Планировщик — {'PRO' if mode == 'pro' else 'Flood'} тест</b>\n\n"
        "Через сколько запустить тест?",
        reply_markup=schedule_delay_kb(mode),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sched_delay:"))
async def cb_sched_delay(cb: CallbackQuery, state: FSMContext):
    _, mode, minutes_str = cb.data.split(":")
    minutes = int(minutes_str)
    await state.set_state(UserStates.schedule_waiting_url)
    await state.update_data(sched_mode=mode, sched_minutes=minutes)

    time_label = {30: "30 минут", 60: "1 час", 180: "3 часа", 360: "6 часов"}.get(minutes, f"{minutes} мин")
    await _delete_and_send(
        cb,
        f"⏱ Запуск через <b>{time_label}</b>\n\nОтправьте URL цели:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(UserStates.schedule_waiting_url)
async def handle_schedule_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id

    if not storage.is_pro(uid):
        await message.answer("Требуется PRO подписка.")
        return

    raw_url = message.text.strip()
    ok, url_or_err = validate_target_url(raw_url)
    if not ok:
        if "заблокирован" in url_or_err or "суффикс" in url_or_err:
            user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
            sec = storage.track_blocked_attempt(uid, raw_url, url_or_err, user_ip)
            await _notify_admin_blocked(message.bot, uid, raw_url, url_or_err,
                                        user_ip, sec["action"], sec["attempts"])
            if sec["action"] == "banned":
                await message.answer("🚫 Вы заблокированы за попытку атаки защищённого ресурса.")
                return
        await message.answer(f"Ошибка: {url_or_err}", reply_markup=cancel_kb())
        return

    from datetime import timedelta
    mode    = data.get("sched_mode", "pro")
    minutes = data.get("sched_minutes", 60)
    run_at  = datetime.utcnow() + timedelta(minutes=minutes)

    intensity_map = {"pro": "high", "flood": "ultra"}
    intensity = intensity_map.get(mode, "high")
    duration  = 120 if mode == "pro" else 60

    user_ip = (storage.get_user(uid) or {}).get("ip_address") or "unknown"
    enqueue_task(uid, "load_test", {
        "target_url": url_or_err,
        "mode": mode,
        "duration": duration,
        "intensity": intensity,
        "method_type": "auto",
        "user_ip": user_ip,
    }, scheduled_for=run_at)

    time_label = {30: "30 минут", 60: "1 час", 180: "3 часа", 360: "6 часов"}.get(minutes, f"{minutes} мин")
    run_at_str = run_at.strftime("%H:%M UTC")
    await message.answer(
        f"⏱ <b>Тест запланирован!</b>\n\n"
        f"🎯 <code>{escape(url_or_err)}</code>\n"
        f"🔧 Режим: <b>{'PRO' if mode == 'pro' else 'Flood'}</b>\n"
        f"⏰ Запуск в: <b>{run_at_str}</b> (через {time_label})\n\n"
        "Уведомление придёт когда тест начнётся и завершится.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )
