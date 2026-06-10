import asyncio
import random
import validators
from html import escape

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import CHANNEL_USERNAME, ADMIN_ID
from bot.keyboards import (
    subscribe_kb, main_menu_kb, back_to_menu_kb, cancel_kb,
    stress_start_kb,
)
from bot.utils.site_analyzer import analyze_site, format_report, _calc_score
from bot.utils.stress_test import run_stress_test, format_stress_report
from bot.utils.ssl_checker import check_ssl, format_ssl_report
from bot.utils.dns_checker import dns_lookup, check_ports, COMMON_PORTS, format_dns_report
from bot.utils.ddos_checker import check_ddos_protection, format_ddos_report
from bot.utils.helpers import safe_edit
from bot.storage import storage

router = Router()


class UserState(StatesGroup):
    waiting_for_url = State()
    stress_waiting_url = State()
    stress_choosing_intensity = State()
    ssl_waiting_url = State()
    dns_waiting_url = State()
    ddos_waiting_ip = State()


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


def _split_text(text: str, limit: int = 4000) -> list[str]:
    lines = text.split("\n")
    parts, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        parts.append(current)
    return parts


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id

    if storage.is_banned(user_id):
        await message.answer("🚫 Ты заблокирован в этом боте.", reply_markup=ReplyKeyboardRemove())
        return

    storage.upsert_user(user_id, message.from_user.first_name, message.from_user.username)
    is_subscribed = await check_subscription(bot, user_id)
    first_name = escape(message.from_user.first_name)

    if not is_subscribed:
        await message.answer(
            f"👋 Привет, <b>{first_name}</b>!\n\n"
            "Для использования бота подпишись на наш канал 📢\n\n"
            "После подписки нажми <b>«✅ Проверить подписку»</b>",
            reply_markup=subscribe_kb(),
        )
        return

    has_sub = storage.has_active_sub(user_id)
    banner = storage.get_banner()
    welcome_text = (
        f"Привет, <b>{first_name}</b> 👋\n\n"
        "Скинь ссылку на сайт — покажу что там за SEO, безопасность, технологии и скорость.\n\n"
        "Или запусти стресс-тест чтобы проверить как сайт держит нагрузку 🔥"
    )
    kb = main_menu_kb(has_sub)
    if banner:
        await message.answer_photo(photo=banner, caption=welcome_text, reply_markup=kb)
    else:
        await message.answer(welcome_text, reply_markup=ReplyKeyboardRemove())
        await message.answer("👇 Главное меню:", reply_markup=kb)


# ─── Channel subscription check ───────────────────────────────────────────────

@router.callback_query(F.data == "checksub")
async def cb_check_subscription(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    is_subscribed = await check_subscription(bot, user_id)
    if is_subscribed:
        has_sub = storage.has_active_sub(user_id)
        await safe_edit(
            callback,
            "✅ <b>Подписка подтверждена!</b>\n\n"
            "Нажми кнопку ниже чтобы начать 👇",
            reply_markup=main_menu_kb(has_sub),
        )
    else:
        await callback.answer(
            "❌ Ты ещё не подписался на канал!\nПодпишись и нажми кнопку снова.",
            show_alert=True,
        )


# ─── Main menu / cancel ───────────────────────────────────────────────────────

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    has_sub = storage.has_active_sub(callback.from_user.id)
    await safe_edit(callback, "👇 Главное меню:", reply_markup=main_menu_kb(has_sub))
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    has_sub = storage.has_active_sub(callback.from_user.id)
    await safe_edit(callback, "↩️ Отменено. Главное меню:", reply_markup=main_menu_kb(has_sub))
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ─── Analyze site ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "analyze")
async def cb_analyze(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await safe_edit(callback, "❌ Для использования бота нужно подписаться на канал.", reply_markup=subscribe_kb())
        await callback.answer()
        return

    if not storage.can_analyze(user_id):
        limit = storage.get_free_limit()
        await safe_edit(
            callback,
            f"⚠️ <b>Исчерпан дневной лимит ({limit}/день)</b>\n\n"
            "Оформи подписку для безлимитного использования 💎",
            reply_markup=main_menu_kb(False),
        )
        await callback.answer()
        return

    await state.set_state(UserState.waiting_for_url)
    await safe_edit(
        callback,
        "🔗 <b>Введи ссылку на сайт</b>\n\nПример: <code>https://example.com</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(UserState.waiting_for_url)
async def process_url(message: Message, state: FSMContext, bot: Bot):
    url = (message.text or "").strip()
    user_id = message.from_user.id
    storage.touch_user(user_id)

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if not validators.url(url):
        await message.answer(
            "❌ Некорректная ссылка.\nПример: <code>https://example.com</code>",
            reply_markup=cancel_kb(),
        )
        return

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await state.clear()
        await message.answer("❌ Ты отписался от канала!", reply_markup=subscribe_kb())
        return

    if not storage.can_analyze(user_id):
        await state.clear()
        limit = storage.get_free_limit()
        await message.answer(
            f"⚠️ Исчерпан лимит: {limit} анализов в день\n\nОформи подписку 💎",
            reply_markup=main_menu_kb(storage.has_active_sub(user_id)),
        )
        return

    await state.clear()
    await _run_analysis(message, url, bot)


async def _run_analysis(message: Message, url: str, bot: Bot):
    user_id = message.from_user.id
    has_sub = storage.has_active_sub(user_id)

    processing = await message.answer(
        "⏳ <b>Анализирую сайт…</b>\n\nПроверяю SEO, безопасность, производительность и технологии 🔍"
    )

    data = await analyze_site(url)
    score, _ = _calc_score(data)
    report = format_report(data)

    if not has_sub:
        storage.use_free_analysis(user_id)
    storage.record_analysis(user_id)
    storage.add_history(user_id, url, score)

    await processing.delete()

    kb = back_to_menu_kb(storage.has_active_sub(user_id))
    chunks = _split_text(report)
    for i, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode="HTML", reply_markup=kb if i == len(chunks) - 1 else None)


# ─── History ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "history")
async def cb_history(callback: CallbackQuery):
    history = storage.get_history(callback.from_user.id)
    has_sub = storage.has_active_sub(callback.from_user.id)

    if not history:
        await safe_edit(
            callback,
            "📋 <b>История пуста</b>\n\nАнализируй сайты — они появятся здесь.",
            reply_markup=main_menu_kb(has_sub),
        )
        await callback.answer()
        return

    lines = ["📋 <b>Последние анализы:</b>\n"]
    for i, entry in enumerate(history, 1):
        score = entry.get("score", 0)
        em = "🟢" if score >= 85 else "🟡" if score >= 65 else "🟠" if score >= 45 else "🔴"
        lines.append(f"{i}. {em} <code>{escape(entry['url'])}</code>")
        lines.append(f"   <b>{score}/100</b> • {entry['date']}")

    await safe_edit(callback, "\n".join(lines), reply_markup=main_menu_kb(has_sub))
    await callback.answer()


# ─── Help ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    has_sub = storage.has_active_sub(callback.from_user.id)
    limit = storage.get_free_limit()
    await safe_edit(
        callback,
        "📖 <b>Возможности бота:</b>\n\n"
        "<b>🔍 Анализ сайта:</b>\n"
        "• SEO: title, description, h1, OG, Twitter Card\n"
        "• Безопасность: 6 HTTP-заголовков\n"
        "• Производительность: скорость, скрипты, размер\n"
        "• Технологии: CMS, фреймворки, сервер\n"
        "• Аналитика: GA, GTM, Яндекс.Метрика\n"
        "• robots.txt, sitemap.xml, favicon, HTTPS\n\n"
        "<b>🔐 SSL-сертификат:</b>\n"
        "• Срок действия, кому выдан, кем подписан\n"
        "• Протокол (TLS 1.2/1.3), шифр\n"
        "• Все домены (SAN)\n\n"
        "<b>🌐 DNS / IP:</b>\n"
        "• IP-адреса, страна, хостинг, ASN\n"
        "• Сканирование открытых портов\n\n"
        "<b>🛡️ Проверка DDoS-защиты:</b>\n"
        "• Введи свой публичный IP-адрес\n"
        "• Бот проверит, реально ли провайдер\n"
        "  защищает тебя от DDoS-атак\n"
        "• Полезно если провайдер обещал защиту,\n"
        "  а интернет всё равно падает\n\n"
        "<b>🔥 Стресс-тест:</b>\n"
        "• До 10 000 запросов / 500 потоков\n"
        "• RPS, P50/P95/P99, статусы, ошибки\n\n"
        f"🆓 Бесплатно: <b>{limit} анализов/день</b>\n"
        "💎 Подписка: безлимитно + стресс-тест\n\n"
        "По вопросам: @hayder_projectx",
        reply_markup=main_menu_kb(has_sub),
    )
    await callback.answer()


# ─── SSL checker ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ssl")
async def cb_ssl(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.ssl_waiting_url)
    await safe_edit(
        callback,
        "🔐 <b>Проверка SSL-сертификата</b>\n\n"
        "Отправь домен или ссылку:\n"
        "<code>example.com</code> или <code>https://example.com</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(UserState.ssl_waiting_url)
async def process_ssl_url(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    hostname = raw.removeprefix("https://").removeprefix("http://").split("/")[0].split("?")[0]

    if not hostname or "." not in hostname:
        await message.answer("❌ Введи корректный домен, например: <code>example.com</code>", reply_markup=cancel_kb())
        return

    await state.clear()
    wait = await message.answer(f"🔐 Проверяю SSL для <code>{escape(hostname)}</code>…")
    data = await check_ssl(hostname)
    await wait.delete()
    report = format_ssl_report(hostname, data)
    has_sub = storage.has_active_sub(message.from_user.id)
    await message.answer(report, parse_mode="HTML", reply_markup=back_to_menu_kb(has_sub))


# ─── DNS / IP checker ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "dns")
async def cb_dns(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.dns_waiting_url)
    await safe_edit(
        callback,
        "🌐 <b>DNS / IP / Порты</b>\n\n"
        "Отправь домен или ссылку:\n"
        "<code>example.com</code> или <code>https://example.com</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(UserState.dns_waiting_url)
async def process_dns_url(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    hostname = raw.removeprefix("https://").removeprefix("http://").split("/")[0].split("?")[0]

    if not hostname or "." not in hostname:
        await message.answer("❌ Введи корректный домен, например: <code>example.com</code>", reply_markup=cancel_kb())
        return

    await state.clear()
    wait = await message.answer(f"🌐 Смотрю DNS и сканирую порты <code>{escape(hostname)}</code>…\n⏳ ~10 сек")
    dns = await dns_lookup(hostname)
    ports = await check_ports(hostname, list(COMMON_PORTS.keys()))
    await wait.delete()
    report = format_dns_report(hostname, dns, ports)
    has_sub = storage.has_active_sub(message.from_user.id)
    await message.answer(report, parse_mode="HTML", reply_markup=back_to_menu_kb(has_sub))


# ─── DDoS protection checker ──────────────────────────────────────────────────

@router.callback_query(F.data == "ddos_check")
async def cb_ddos_check(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.ddos_waiting_ip)
    await safe_edit(
        callback,
        "🛡️ <b>Проверка DDoS-защиты интернета</b>\n\n"
        "Эта проверка анализирует, действительно ли ваш провайдер обеспечивает "
        "защиту от DDoS-атак, как заявляет.\n\n"
        "📌 <b>Как узнать свой IP-адрес?</b>\n"
        "Перейдите на <a href=\"https://2ip.ru\">2ip.ru</a> или "
        "<a href=\"https://whatismyip.com\">whatismyip.com</a> и скопируйте адрес.\n\n"
        "✏️ <b>Введите ваш публичный IP-адрес:</b>\n"
        "Пример: <code>85.142.10.55</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(UserState.ddos_waiting_ip)
async def process_ddos_ip(message: Message, state: FSMContext):
    raw = (message.text or "").strip()

    import re
    raw = re.sub(r"^https?://", "", raw).split("/")[0].split(":")[0].strip()

    if not raw or (
        not re.match(r"^(\d{1,3}\.){3}\d{1,3}$", raw)
        and not re.match(r"^[0-9a-fA-F:]{2,39}$", raw)
    ):
        await message.answer(
            "❌ Некорректный IP-адрес.\n"
            "Введи IPv4, например: <code>85.142.10.55</code>",
            reply_markup=cancel_kb(),
        )
        return

    await state.clear()
    wait = await message.answer(
        f"🛡️ Проверяю <code>{escape(raw)}</code> на наличие DDoS-защиты…\n"
        "⏳ ~10–15 сек"
    )
    data = await check_ddos_protection(raw)
    await wait.delete()
    report = format_ddos_report(data)
    has_sub = storage.has_active_sub(message.from_user.id)
    await message.answer(report, parse_mode="HTML", reply_markup=back_to_menu_kb(has_sub))


# ─── Stress test ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "stress")
async def cb_stress(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    if not storage.has_active_sub(user_id):
        await safe_edit(
            callback,
            "🔥 <b>Стресс-тест</b>\n\n"
            "Доступен только с подпиской 💎",
            reply_markup=main_menu_kb(False),
        )
        await callback.answer()
        return

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await safe_edit(callback, "❌ Нужно подписаться на канал.", reply_markup=subscribe_kb())
        await callback.answer()
        return

    await state.set_state(UserState.stress_waiting_url)
    await safe_edit(
        callback,
        "🔥 <b>Стресс-тест</b>\n\n"
        "Введи цель для теста:\n\n"
        "• IP адрес: <code>185.24.10.5</code>\n"
        "• IP с портом: <code>185.24.10.5:8080</code>\n"
        "• Домен: <code>mysite.com</code>\n"
        "• Полная ссылка: <code>https://mysite.com</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(UserState.stress_waiting_url)
async def process_stress_url(message: Message, state: FSMContext):
    import re
    raw = (message.text or "").strip()

    # Detect bare IP (optionally with port): e.g. 185.24.10.5 or 185.24.10.5:8080
    ip_pattern = re.compile(
        r'^(\d{1,3}\.){3}\d{1,3}(:\d+)?(/\S*)?$'
    )
    if ip_pattern.match(raw):
        url = "http://" + raw
    elif not raw.startswith("http://") and not raw.startswith("https://"):
        url = "https://" + raw
    else:
        url = raw

    if not validators.url(url):
        await message.answer(
            "❌ Некорректный адрес.\n\n"
            "Можно вводить:\n"
            "• IP адрес: <code>185.24.10.5</code>\n"
            "• IP с портом: <code>185.24.10.5:8080</code>\n"
            "• Домен: <code>mysite.com</code>\n"
            "• Полная ссылка: <code>https://mysite.com</code>",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(stress_url=url)
    await state.set_state(UserState.stress_choosing_intensity)

    await message.answer(
        f"🎯 <code>{escape(url)}</code>\n\n"
        "Выбери интенсивность теста:",
        reply_markup=stress_start_kb(),
    )


def _bar(done: int, total: int, width: int = 16) -> str:
    filled = int(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _sep() -> str:
    return "─" * 24


@router.callback_query(F.data.startswith("stress_run:"), UserState.stress_choosing_intensity)
async def cb_stress_run(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if not storage.has_active_sub(user_id):
        await callback.answer("❌ Требуется подписка", show_alert=True)
        return

    parts = callback.data.split(":")
    total = int(parts[1])
    concurrency = int(parts[2])

    data = await state.get_data()
    url = data.get("stress_url")
    await state.clear()

    if not url:
        await callback.answer("❌ URL не найден. Начни заново.", show_alert=True)
        return

    await callback.answer()

    hostname = url.removeprefix("https://").removeprefix("http://").split("/")[0]
    sep = _sep()
    scan_lines: list[str] = []

    # Phase 1 — port scan (real)
    msg = await callback.message.answer(
        f"⚡ <b>НАГРУЗОЧНЫЙ ТЕСТ</b>\n"
        f"{sep}\n"
        f"🎯 <code>{escape(hostname)}</code>\n"
        f"{sep}\n\n"
        f"🔍 Сканирую открытые порты…"
    )

    async def on_scan(line: str):
        scan_lines.append(line)
        try:
            await msg.edit_text(
                f"⚡ <b>НАГРУЗОЧНЫЙ ТЕСТ</b>\n"
                f"{sep}\n"
                f"🎯 <code>{escape(hostname)}</code>\n"
                f"{sep}\n\n"
                + "\n".join(scan_lines),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Phase 2 — countdown 3…2…1
    async def do_countdown():
        for n in (3, 2, 1):
            try:
                await msg.edit_text(
                    f"⚡ <b>НАГРУЗОЧНЫЙ ТЕСТ</b>\n"
                    f"{sep}\n"
                    f"🎯 <code>{escape(hostname)}</code>\n"
                    f"{sep}\n\n"
                    + "\n".join(scan_lines) + f"\n\n🚀 Запуск через <b>{n}…</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await asyncio.sleep(1.0)

    # Phase 3 — live progress during actual test
    mode_label = {"http": "HTTP-флуд", "tcp": "TCP-флуд"}

    async def on_progress(done: int, total_req: int, success: int, failed: int, rps: float):
        pct = done * 100 // total_req
        sr  = success * 100 // done if done else 0
        try:
            await msg.edit_text(
                f"⚡ <b>НАГРУЗОЧНЫЙ ТЕСТ ИДЁТ</b>\n"
                f"{sep}\n"
                f"🎯 <code>{escape(hostname)}</code>\n"
                f"{sep}\n\n"
                f"📤 Отправлено: <b>{done:,}</b> / {total_req:,}\n"
                f"{_bar(done, total_req)}  {pct}%\n\n"
                f"✅ Успешных:  <b>{success:,}</b>  ({sr}%)\n"
                f"❌ Ошибок:    <b>{failed:,}</b>\n"
                f"⚡ RPS:        <b>~{rps:.0f}</b>\n"
                f"{sep}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    try:
        result = await run_stress_test(
            url, total=total, concurrency=concurrency,
            progress_cb=on_progress, scan_cb=on_scan,
        )
        await do_countdown()
    except Exception as e:
        try:
            await msg.edit_text(
                f"❌ <b>Ошибка при тесте</b>\n\n{escape(str(e))}",
                reply_markup=main_menu_kb(True),
            )
        except Exception:
            pass
        return

    # Final report
    report = format_stress_report(url, result)
    try:
        await msg.delete()
    except Exception:
        pass
    await callback.message.answer(report, parse_mode="HTML", reply_markup=main_menu_kb(True))


# ─── Fallback ─────────────────────────────────────────────────────────────────

@router.message()
async def fallback_handler(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id

    if storage.is_banned(user_id):
        return

    storage.upsert_user(user_id, message.from_user.first_name, message.from_user.username)

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await message.answer("❌ Для использования бота подпишись на канал.", reply_markup=subscribe_kb())
        return

    text = (message.text or "").strip()
    # Auto-detect URLs in plain text
    if text.startswith("http") or ("." in text and " " not in text and len(text) > 5):
        candidate = text if text.startswith("http") else "https://" + text
        if validators.url(candidate):
            if not storage.can_analyze(user_id):
                limit = storage.get_free_limit()
                await message.answer(
                    f"⚠️ Исчерпан лимит: {limit}/день\nОформи подписку 💎",
                    reply_markup=main_menu_kb(storage.has_active_sub(user_id)),
                )
                return
            await _run_analysis(message, candidate, bot)
            return

    has_sub = storage.has_active_sub(user_id)
    await message.answer(
        "🤔 Не понял. Отправь ссылку на сайт или нажми кнопку 👇",
        reply_markup=main_menu_kb(has_sub),
    )
