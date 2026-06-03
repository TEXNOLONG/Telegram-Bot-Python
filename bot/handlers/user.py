import validators
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import CHANNEL_USERNAME, CHANNEL_LINK, ADMIN_ID
from bot.keyboards import subscribe_keyboard, main_menu_keyboard, admin_menu_keyboard, cancel_keyboard
from bot.utils.site_analyzer import analyze_site, format_report
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


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id
    storage.add_user(user_id)

    is_subscribed = await check_subscription(bot, user_id)

    if is_subscribed:
        kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
        await message.answer(
            f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
            "Я помогу тебе проанализировать любой сайт — найду ошибки, SEO-проблемы, "
            "проблемы безопасности и производительности.\n\n"
            "Отправь мне ссылку на сайт, и я всё проверю! 🔍",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
            "Для использования бота необходимо подписаться на наш канал 📢\n\n"
            f"После подписки нажми кнопку <b>«✅ Я подписался»</b>",
            reply_markup=subscribe_keyboard(),
        )


@router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    is_subscribed = await check_subscription(bot, user_id)

    if is_subscribed:
        kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
        await callback.message.edit_text(
            "✅ <b>Отлично! Подписка подтверждена.</b>\n\n"
            "Теперь ты можешь пользоваться ботом.\n"
            "Отправь мне ссылку на сайт для анализа 🔍"
        )
        await callback.message.answer("Выбери действие:", reply_markup=kb)
    else:
        await callback.answer(
            "❌ Ты ещё не подписался на канал!\nПодпишись и нажми кнопку снова.",
            show_alert=True,
        )


@router.message(F.text == "🔍 Анализировать сайт")
async def btn_analyze(message: Message, state: FSMContext, bot: Bot):
    is_subscribed = await check_subscription(bot, message.from_user.id)
    if not is_subscribed:
        await state.clear()
        await message.answer(
            "❌ Для использования бота нужно подписаться на канал.",
            reply_markup=subscribe_keyboard(),
        )
        return
    await state.set_state(UserState.waiting_for_url)
    await message.answer(
        "🔗 Отправь ссылку на сайт, который хочешь проанализировать.\n\n"
        "Пример: <code>https://example.com</code>",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
    await message.answer("↩️ Отменено.", reply_markup=kb)


@router.message(F.text == "ℹ️ Помощь")
async def btn_help(message: Message):
    await message.answer(
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«🔍 Анализировать сайт»</b>\n"
        "2️⃣ Отправь ссылку на любой сайт\n"
        "3️⃣ Получи подробный отчёт\n\n"
        "<b>Что проверяет бот:</b>\n"
        "• 🔎 SEO: title, description, h1, Open Graph, canonical\n"
        "• 🛡 Безопасность: HTTP-заголовки\n"
        "• ⚡ Производительность: скорость ответа, размер, скрипты\n"
        "• ♿ Доступность: alt у картинок, label у форм\n"
        "• 🔗 Ссылки: пустые и внешние\n\n"
        "По вопросам: @hayder_projectx",
    )


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

    is_subscribed = await check_subscription(bot, message.from_user.id)
    if not is_subscribed:
        await state.clear()
        await message.answer(
            "❌ Ты отписался от канала! Для продолжения необходима подписка.",
            reply_markup=subscribe_keyboard(),
        )
        return

    await state.clear()
    processing_msg = await message.answer("⏳ Анализирую сайт, подожди немного...")

    data = await analyze_site(url)
    storage.increment_analyses()
    report = format_report(data)

    await processing_msg.delete()
    user_id = message.from_user.id
    kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
    await message.answer(report, parse_mode="HTML", reply_markup=kb)


@router.message()
async def fallback_handler(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    storage.add_user(user_id)

    is_subscribed = await check_subscription(bot, user_id)
    if not is_subscribed:
        await message.answer(
            "❌ Для использования бота нужно подписаться на канал.",
            reply_markup=subscribe_keyboard(),
        )
        return

    text = (message.text or "").strip()
    if text.startswith("http") or ("." in text and " " not in text and len(text) > 4):
        if not text.startswith("http"):
            text = "https://" + text
        if validators.url(text):
            processing_msg = await message.answer("⏳ Анализирую сайт, подожди немного...")
            data = await analyze_site(text)
            storage.increment_analyses()
            report = format_report(data)
            await processing_msg.delete()
            kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
            await message.answer(report, parse_mode="HTML", reply_markup=kb)
            return

    kb = admin_menu_keyboard() if user_id == ADMIN_ID else main_menu_keyboard()
    await message.answer(
        "🤔 Не понял команду.\n\n"
        "Отправь ссылку на сайт или нажми <b>«🔍 Анализировать сайт»</b>",
        reply_markup=kb,
    )
