import validators
from html import escape
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import CHANNEL_USERNAME, CHANNEL_LINK, ADMIN_ID
from bot.keyboards import subscribe_keyboard, main_menu_keyboard, admin_menu_keyboard, cancel_keyboard
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


def get_kb(user_id: int):
    return admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id
    storage.add_user(user_id)
    is_subscribed = await check_subscription(bot, user_id)

    if is_subscribed:
        await message.answer(
            f"👋 Привет, <b>{escape(message.from_user.first_name)}</b>!\n\n"
            "Я анализирую сайты и нахожу:\n"
            "• 🔎 SEO-проблемы\n"
            "• 🛡 Уязвимости безопасности\n"
            "• ⚡ Проблемы производительности\n"
            "• ♿ Проблемы доступности\n"
            "• 🛠 Используемые технологии\n\n"
            "Просто отправь мне ссылку на сайт! 🔍",
            reply_markup=get_kb(user_id),
        )
    else:
        await message.answer(
            f"👋 Привет, <b>{escape(message.from_user.first_name)}</b>!\n\n"
            "Для использования бота подпишись на канал 📢\n\n"
            "После подписки нажми <b>«✅ Я подписался»</b>",
            reply_markup=subscribe_keyboard(),
        )


@router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    is_subscribed = await check_subscription(bot, user_id)

    if is_subscribed:
        await callback.message.edit_text(
            "✅ <b>Подписка подтверждена!</b>\n\n"
            "Отправь ссылку на сайт для анализа 🔍"
        )
        await callback.message.answer("Выбери действие:", reply_markup=get_kb(user_id))
    else:
        await callback.answer(
            "❌ Ты ещё не подписался!\nПодпишись на канал и нажми кнопку снова.",
            show_alert=True,
        )


@router.message(F.text == "🔍 Анализировать сайт")
async def btn_analyze(message: Message, state: FSMContext, bot: Bot):
    is_subscribed = await check_subscription(bot, message.from_user.id)
    if not is_subscribed:
        await state.clear()
        await message.answer("❌ Для использования бота нужно подписаться на канал.", reply_markup=subscribe_keyboard())
        return
    await state.set_state(UserState.waiting_for_url)
    await message.answer(
        "🔗 Отправь ссылку на сайт для анализа.\n\n"
        "Пример: <code>https://example.com</code>",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Отменено.", reply_markup=get_kb(message.from_user.id))


@router.message(F.text == "📋 История")
async def btn_history(message: Message):
    history = storage.get_history(message.from_user.id)
    if not history:
        await message.answer(
            "📋 <b>История анализов пуста.</b>\n\nОтправь ссылку на сайт, чтобы начать!",
            reply_markup=get_kb(message.from_user.id),
        )
        return

    lines = ["📋 <b>Последние анализы:</b>\n"]
    for i, entry in enumerate(history, 1):
        score = entry.get("score", "?")
        if isinstance(score, int):
            if score >= 85:
                emoji = "🟢"
            elif score >= 65:
                emoji = "🟡"
            elif score >= 45:
                emoji = "🟠"
            else:
                emoji = "🔴"
        else:
            emoji = "⚪"
        lines.append(f"{i}. {emoji} <code>{escape(entry['url'])}</code>")
        lines.append(f"   Оценка: <b>{score}/100</b>  •  {entry['date']}")

    await message.answer("\n".join(lines), reply_markup=get_kb(message.from_user.id))


@router.message(F.text == "ℹ️ Помощь")
async def btn_help(message: Message):
    await message.answer(
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«🔍 Анализировать сайт»</b>\n"
        "2️⃣ Отправь ссылку на любой сайт\n"
        "3️⃣ Получи подробный отчёт\n\n"
        "<b>Что анализирует бот:</b>\n"
        "• 🔎 SEO: title, description, h1, Open Graph, Twitter Card, canonical, robots\n"
        "• 🛡 Безопасность: 6 HTTP-заголовков, утечка версий\n"
        "• ⚡ Производительность: скорость, размер HTML, скрипты, lazy loading\n"
        "• ♿ Доступность: alt у картинок, label у форм, lang\n"
        "• 🛠 Технологии: CMS, фреймворки, сервер\n"
        "• 📡 Аналитика: GA, GTM, Яндекс.Метрика, Facebook Pixel\n"
        "• 🗺 Инфраструктура: robots.txt, sitemap.xml, favicon, HTTPS\n"
        "• 📝 Структурированные данные (Schema.org / JSON-LD)\n\n"
        "По вопросам: @hayder_projectx",
    )


@router.message(Command("history"))
async def cmd_history(message: Message):
    await btn_history(message)


async def _do_analysis(message: Message, url: str, bot: Bot):
    is_subscribed = await check_subscription(bot, message.from_user.id)
    if not is_subscribed:
        await message.answer("❌ Ты отписался от канала!", reply_markup=subscribe_keyboard())
        return

    processing_msg = await message.answer("⏳ Анализирую сайт, подожди немного…\n\nПроверяю SEO, безопасность, производительность и технологии 🔍")

    data = await analyze_site(url)
    storage.increment_analyses()

    score, _ = _calc_score(data)
    storage.add_history(message.from_user.id, url, score)

    report = format_report(data)

    await processing_msg.delete()

    if len(report) > 4096:
        parts = []
        current = ""
        for line in report.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            parts.append(current)
        for i, part in enumerate(parts):
            kb = get_kb(message.from_user.id) if i == len(parts) - 1 else None
            await message.answer(part, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(report, parse_mode="HTML", reply_markup=get_kb(message.from_user.id))


@router.message(UserState.waiting_for_url)
async def process_url(message: Message, state: FSMContext, bot: Bot):
    url = message.text.strip()

    if url == "❌ Отмена":
        await btn_cancel(message, state)
        return

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if not validators.url(url):
        await message.answer(
            "❌ Это не похоже на корректную ссылку.\n"
            "Пример: <code>https://example.com</code>"
        )
        return

    await state.clear()
    await _do_analysis(message, url, bot)


@router.message()
async def fallback_handler(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    storage.add_user(user_id)

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await message.answer("❌ Для использования бота нужно подписаться на канал.", reply_markup=subscribe_keyboard())
        return

    text = (message.text or "").strip()
    if text.startswith("http") or ("." in text and " " not in text and len(text) > 4):
        if not text.startswith("http"):
            text = "https://" + text
        if validators.url(text):
            await _do_analysis(message, text, bot)
            return

    await message.answer(
        "🤔 Не понял команду.\n\nОтправь ссылку на сайт или нажми <b>«🔍 Анализировать сайт»</b>",
        reply_markup=get_kb(user_id),
    )
