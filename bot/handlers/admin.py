from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_ID
from bot.keyboards import admin_menu_keyboard, cancel_keyboard
from bot.storage import storage

router = Router()


class AdminState(StatesGroup):
    waiting_broadcast = State()


def is_admin(message: Message) -> bool:
    return message.from_user.id == ADMIN_ID


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "👑 <b>Панель администратора</b>\n\n"
        "Доступные команды:\n"
        "• /stats — статистика бота\n"
        "• /broadcast — рассылка сообщения всем пользователям",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(F.text == "📊 Статистика")
async def btn_stats(message: Message):
    if not is_admin(message):
        return
    users = storage.get_all_users()
    analyses = storage.get_total_analyses()
    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{len(users)}</b>\n"
        f"🔍 Анализов выполнено: <b>{analyses}</b>",
    )


@router.message(F.text == "📢 Рассылка")
async def btn_broadcast(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.set_state(AdminState.waiting_broadcast)
    await message.answer(
        "✍️ Напиши сообщение для рассылки всем пользователям.\n"
        "Поддерживается HTML-форматирование.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminState.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message):
        await state.clear()
        return

    await state.clear()
    users = storage.get_all_users()

    if not users:
        await message.answer("❌ Нет пользователей для рассылки.", reply_markup=admin_menu_keyboard())
        return

    sent = 0
    failed = 0
    status_msg = await message.answer(f"📤 Рассылка начата... 0/{len(users)}")

    for i, user_id in enumerate(users, 1):
        try:
            await bot.send_message(user_id, message.text or message.caption or "", parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

        if i % 10 == 0:
            try:
                await status_msg.edit_text(f"📤 Рассылка... {i}/{len(users)}")
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"✉️ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )
    await message.answer("Готово!", reply_markup=admin_menu_keyboard())
