import validators
from html import escape

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import CHANNEL_USERNAME, CHANNEL_LINK, ADMIN_ID
from bot.keyboards import (
    subscribe_kb, main_menu_kb, back_to_menu_kb, cancel_kb,
)
from bot.utils.site_analyzer import analyze_site, format_report, _calc_score
from bot.storage import storage

router = Router()


class UserState(StatesGroup):
    waiting_for_url = State()


async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def send_welcome(target, user_id: int, first_name: str, has_sub: bool, banner: str | None):
    text = (
        f"👋 Привет, <b>{escape(first_name)}</b>!\n\n"
        "Я анализирую сайты и нахожу:\n"
        "• 🔎 SEO-проблемы\n"
        "• 🛡 Уязвимости безопасности\n"
        "• ⚡ Проблемы производительности\n"
        "• 🛠 Используемые технологии\n"
        "• 📡 Системы аналитики\n\n"
        "Нажми кнопку ниже чтобы начать 👇"
    )
    kb = main_menu_kb(has_sub)
    if banner:
        if hasattr(target, "answer_photo"):
            await target.answer_photo(photo=banner, caption=text, reply_markup=kb)
        else:
            await target.edit_media
    else:
        if hasattr(target, "answer"):
            await target.answer(text, reply_markup=kb, reply_markup_remove=None)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id

    if storage.is_banned(user_id):
        await message.answer(
            "🚫 Ты заблокирован в этом боте.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    storage.upsert_user(user_id, message.from_user.first_name, message.from_user.username)
    is_subscribed = await check_subscription(bot, user_id)

    await message.answer(".", reply_markup=ReplyKeyboardRemove())
    import asyncio
    await asyncio.sleep(0.1)

    if not is_subscribed:
        await message.answer(
            f"👋 Привет, <b>{escape(message.from_user.first_name)}</b>!\n\n"
            "Для использования бота подпишись на наш канал 📢\n\n"
            "После подписки нажми <b>«✅ Проверить подписку»</b>",
            reply_markup=subscribe_kb(),
        )
        return

    has_sub = storage.has_active_sub(user_id)
    banner = storage.get_banner()
    text = (
        f"👋 Привет, <b>{escape(message.from_user.first_name)}</b>!\n\n"
        "Я анализирую сайты и нахожу:\n"
        "• 🔎 SEO-проблемы\n"
        "• 🛡 Уязвимости безопасности\n"
        "• ⚡ Проблемы производительности\n"
        "• 🛠 Используемые технологии\n"
        "• 📡 Системы аналитики\n\n"
        "Нажми кнопку ниже чтобы начать 👇"
    )
    kb = main_menu_kb(has_sub)
    if banner:
        await message.answer_photo(photo=banner, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "checksub")
async def cb_check_subscription(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    is_subscribed = await check_subscription(bot, user_id)

    if is_subscribed:
        has_sub = storage.has_active_sub(user_id)
        await callback.message.edit_text(
            "✅ <b>Подписка подтверждена!</b>\n\n"
            "Нажми кнопку ниже чтобы начать 👇",
            reply_markup=main_menu_kb(has_sub),
        )
    else:
        await callback.answer(
            "❌ Ты ещё не подписался на канал!\nПодпишись и нажми кнопку снова.",
            show_alert=True,
        )


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    has_sub = storage.has_active_sub(user_id)
    try:
        await callback.message.edit_text(
            "Выбери действие 👇",
            reply_markup=main_menu_kb(has_sub),
        )
    except Exception:
        await callback.message.answer(
            "Выбери действие 👇",
            reply_markup=main_menu_kb(has_sub),
        )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    has_sub = storage.has_active_sub(user_id)
    try:
        await callback.message.edit_text(
            "↩️ Отменено. Выбери действие 👇",
            reply_markup=main_menu_kb(has_sub),
        )
    except Exception:
        await callback.message.answer(
            "↩️ Отменено.",
            reply_markup=main_menu_kb(has_sub),
        )
    await callback.answer()


@router.callback_query(F.data == "analyze")
async def cb_analyze(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await callback.message.edit_text(
            "❌ Для использования бота нужно подписаться на канал.",
            reply_markup=subscribe_kb(),
        )
        await callback.answer()
        return

    if not storage.can_analyze(user_id):
        limit = storage.get_free_limit()
        await callback.message.edit_text(
            f"⚠️ <b>Исчерпан дневной лимит</b>\n\n"
            f"🆓 Бесплатно: {limit} анализов в день\n\n"
            "Оформи подписку для безлимитного использования 💎",
            reply_markup=main_menu_kb(False),
        )
        await callback.answer()
        return

    await state.set_state(UserState.waiting_for_url)
    await callback.message.edit_text(
        "🔗 <b>Введи ссылку на сайт</b>\n\n"
        "Пример: <code>https://example.com</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "history")
async def cb_history(callback: CallbackQuery):
    history = storage.get_history(callback.from_user.id)
    has_sub = storage.has_active_sub(callback.from_user.id)

    if not history:
        await callback.message.edit_text(
            "📋 <b>История пуста</b>\n\nАнализируй сайты — они появятся здесь.",
            reply_markup=main_menu_kb(has_sub),
        )
        await callback.answer()
        return

    lines = ["📋 <b>Последние анализы:</b>\n"]
    for i, entry in enumerate(history, 1):
        score = entry.get("score", 0)
        if score >= 85:
            em = "🟢"
        elif score >= 65:
            em = "🟡"
        elif score >= 45:
            em = "🟠"
        else:
            em = "🔴"
        lines.append(f"{i}. {em} <code>{escape(entry['url'])}</code>")
        lines.append(f"   <b>{score}/100</b> • {entry['date']}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=main_menu_kb(has_sub),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    has_sub = storage.has_active_sub(callback.from_user.id)
    limit = storage.get_free_limit()
    await callback.message.edit_text(
        "📖 <b>Как пользоваться:</b>\n\n"
        "1️⃣ Нажми <b>«🔍 Анализировать сайт»</b>\n"
        "2️⃣ Отправь ссылку\n"
        "3️⃣ Получи отчёт с оценкой 0–100\n\n"
        "<b>Что проверяет бот:</b>\n"
        "• 🔎 SEO: title, description, h1, OG, Twitter Card\n"
        "• 🛡 Безопасность: HTTP-заголовки, утечка версий\n"
        "• ⚡ Производительность: скорость, размер, скрипты\n"
        "• ♿ Доступность: alt у картинок, label у форм\n"
        "• 🛠 Технологии: CMS, фреймворки, сервер\n"
        "• 📡 Аналитика: GA, GTM, Яндекс.Метрика\n"
        "• 🗺 robots.txt, sitemap.xml, favicon, HTTPS\n\n"
        f"🆓 Бесплатно: <b>{limit} анализов в день</b>\n"
        "💎 Подписка: безлимитно\n\n"
        "По вопросам: @hayder_projectx",
        reply_markup=main_menu_kb(has_sub),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
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
            "❌ Некорректная ссылка.\n"
            "Пример: <code>https://example.com</code>",
            reply_markup=cancel_kb(),
        )
        return

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await state.clear()
        await message.answer(
            "❌ Ты отписался от канала!",
            reply_markup=subscribe_kb(),
        )
        return

    if not storage.can_analyze(user_id):
        await state.clear()
        limit = storage.get_free_limit()
        has_sub = storage.has_active_sub(user_id)
        await message.answer(
            f"⚠️ <b>Исчерпан лимит: {limit} анализов в день</b>\n\n"
            "Оформи подписку для безлимитного использования 💎",
            reply_markup=main_menu_kb(has_sub),
        )
        return

    await state.clear()
    await _run_analysis(message, url, bot)


async def _run_analysis(message: Message, url: str, bot: Bot):
    user_id = message.from_user.id
    has_sub = storage.has_active_sub(user_id)

    processing = await message.answer(
        "⏳ <b>Анализирую сайт…</b>\n\n"
        "Проверяю SEO, безопасность, производительность и технологии 🔍"
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

    if len(report) > 4096:
        chunks = _split_text(report, 4000)
        for i, chunk in enumerate(chunks):
            await message.answer(
                chunk,
                parse_mode="HTML",
                reply_markup=kb if i == len(chunks) - 1 else None,
            )
    else:
        await message.answer(report, parse_mode="HTML", reply_markup=kb)


def _split_text(text: str, limit: int) -> list[str]:
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
    if text.startswith("http") or ("." in text and " " not in text and len(text) > 5):
        candidate = text if text.startswith("http") else "https://" + text
        if validators.url(candidate):
            if not storage.can_analyze(user_id):
                limit = storage.get_free_limit()
                await message.answer(
                    f"⚠️ Исчерпан лимит: {limit} анализов в день\n\n"
                    "Оформи подписку 💎",
                    reply_markup=main_menu_kb(False),
                )
                return
            await _run_analysis(message, candidate, bot)
            return

    has_sub = storage.has_active_sub(user_id)
    await message.answer(
        "🤔 Не понял. Отправь ссылку на сайт или нажми кнопку 👇",
        reply_markup=main_menu_kb(has_sub),
    )
