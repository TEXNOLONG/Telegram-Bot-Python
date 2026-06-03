import logging
from html import escape
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.filters import Command, BaseFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_ID, SUBSCRIPTION_PLANS, USERS_PER_PAGE, PAYMENTS_PER_PAGE
from bot.keyboards import (
    admin_main_kb, admin_back_kb, admin_users_kb, admin_user_kb,
    admin_give_sub_kb, admin_broadcast_kb, admin_banner_kb,
    admin_settings_kb, admin_payments_kb, cancel_kb, confirm_kb,
)
from bot.storage import storage

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        if hasattr(event, "from_user") and event.from_user:
            return event.from_user.id == ADMIN_ID
        return False


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminState(StatesGroup):
    broadcast_text = State()
    broadcast_photo = State()
    broadcast_caption = State()
    banner_set = State()
    set_price = State()
    set_limit = State()
    ban_id = State()


# ─── /admin command ───────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👑 <b>Панель администратора</b>\n\nВыбери раздел:",
        reply_markup=admin_main_kb(),
    )


# ─── Main admin menu ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm")
async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text(
            "👑 <b>Панель администратора</b>\n\nВыбери раздел:",
            reply_markup=admin_main_kb(),
        )
    except Exception:
        await callback.message.answer(
            "👑 <b>Панель администратора</b>\n\nВыбери раздел:",
            reply_markup=admin_main_kb(),
        )
    await callback.answer()


# ─── Statistics ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_stats")
async def cb_admin_stats(callback: CallbackQuery):
    total = storage.total_users()
    subs = storage.subscribed_count()
    banned = storage.banned_count()
    analyses = storage.total_analyses()
    revenue = storage.total_revenue()
    new_today = storage.new_users_today()

    await callback.message.edit_text(
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🆕 Новых сегодня: <b>{new_today}</b>\n"
        f"💎 Активных подписок: <b>{subs}</b>\n"
        f"🆓 Бесплатных: <b>{total - subs - banned}</b>\n"
        f"🚫 Заблокировано: <b>{banned}</b>\n\n"
        f"🔍 Анализов выполнено: <b>{analyses}</b>\n"
        f"💰 Общая выручка: <b>${revenue:.2f} USDT</b>\n\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        reply_markup=admin_back_kb(),
    )
    await callback.answer()


# ─── Users list ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_users:"))
async def cb_admin_users(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    all_users = storage.get_all_users_list()
    total = len(all_users)
    start = page * USERS_PER_PAGE
    page_users = all_users[start: start + USERS_PER_PAGE]

    if not page_users:
        await callback.answer("Нет пользователей", show_alert=True)
        return

    await callback.message.edit_text(
        f"👥 <b>Пользователи</b> ({total} всего)\n\n"
        "💎 = подписка   🚫 = бан\n"
        "Нажми на пользователя для управления:",
        reply_markup=admin_users_kb(page_users, page, total),
    )
    await callback.answer()


# ─── User detail ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_u:"))
async def cb_admin_user(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    u = storage.get_user(user_id)

    fn = escape(u.get("first_name", "—"))
    un = f"@{u['username']}" if u.get("username") else "нет"
    first_seen = u.get("first_seen", "—")[:10]
    last_active = u.get("last_active", "—")[:16].replace("T", " ")
    is_banned = u.get("banned", False)
    has_sub = storage.has_active_sub(user_id)
    expires = storage.sub_expires_str(user_id)
    total_an = u.get("total_analyses", 0)

    sub_status = (
        f"💎 Активна до {expires}" if has_sub
        else "🆓 Нет подписки"
    )
    ban_status = "🚫 Да" if is_banned else "✅ Нет"

    await callback.message.edit_text(
        f"👤 <b>{fn}</b> ({un})\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Регистрация: {first_seen}\n"
        f"🕒 Последняя активность: {last_active}\n"
        f"💎 Подписка: {sub_status}\n"
        f"🔍 Анализов: {total_an}\n"
        f"🚫 Бан: {ban_status}",
        reply_markup=admin_user_kb(user_id, is_banned, has_sub),
    )
    await callback.answer()


# ─── Ban / Unban ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_ban:"))
async def cb_admin_ban(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    u = storage.get_user(user_id)
    fn = escape(u.get("first_name", str(user_id)))
    await callback.message.edit_text(
        f"🚫 Заблокировать пользователя <b>{fn}</b> (ID: <code>{user_id}</code>)?",
        reply_markup=confirm_kb(f"adm_ban_ok:{user_id}", f"adm_u:{user_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_ban_ok:"))
async def cb_admin_ban_ok(callback: CallbackQuery, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    storage.ban_user(user_id)
    try:
        await bot.send_message(user_id, "🚫 Ты заблокирован в этом боте.")
    except Exception:
        pass
    await callback.message.edit_text(
        f"✅ Пользователь <code>{user_id}</code> заблокирован.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer("Заблокирован")


@router.callback_query(F.data.startswith("adm_uban:"))
async def cb_admin_unban(callback: CallbackQuery, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    storage.unban_user(user_id)
    try:
        await bot.send_message(user_id, "✅ Ты разблокирован. Нажми /start чтобы продолжить.")
    except Exception:
        pass
    await callback.message.edit_text(
        f"✅ Пользователь <code>{user_id}</code> разблокирован.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer("Разблокирован")


# ─── Give subscription ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_gs:"))
async def cb_admin_give_sub(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    u = storage.get_user(user_id)
    fn = escape(u.get("first_name", str(user_id)))
    await callback.message.edit_text(
        f"💎 Выдать подписку пользователю <b>{fn}</b>.\n\nВыбери срок:",
        reply_markup=admin_give_sub_kb(user_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_gsp:"))
async def cb_admin_give_sub_plan(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    user_id = int(parts[1])
    plan = parts[2]
    if plan not in SUBSCRIPTION_PLANS:
        await callback.answer("❌ Неверный план", show_alert=True)
        return

    plan_info = SUBSCRIPTION_PLANS[plan]
    storage.activate_subscription(user_id, plan, plan_info["days"])
    expires = storage.sub_expires_str(user_id)

    try:
        await bot.send_message(
            user_id,
            f"🎁 <b>Администратор выдал тебе подписку!</b>\n\n"
            f"{plan_info['emoji']} <b>{plan_info['label']}</b>\n"
            f"📅 Действует до: {expires}",
        )
    except Exception:
        pass

    await callback.message.edit_text(
        f"✅ Подписка «{plan_info['label']}» выдана пользователю <code>{user_id}</code>.\n"
        f"Действует до: <b>{expires}</b>",
        reply_markup=admin_back_kb(),
    )
    await callback.answer("Выдано!")


# ─── Ban by ID ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_banid")
async def cb_admin_ban_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.ban_id)
    await callback.message.edit_text(
        "🚫 Введи Telegram ID пользователя для блокировки:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminState.ban_id)
async def process_ban_id(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный ID. Введи число.", reply_markup=admin_main_kb())
        return

    storage.ban_user(user_id)
    try:
        await bot.send_message(user_id, "🚫 Ты заблокирован.")
    except Exception:
        pass
    await message.answer(
        f"✅ Пользователь <code>{user_id}</code> заблокирован.",
        reply_markup=admin_main_kb(),
    )


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_bcast")
async def cb_admin_broadcast(callback: CallbackQuery):
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\nВыбери тип рассылки:",
        reply_markup=admin_broadcast_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm_btext")
async def cb_admin_broadcast_text(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.broadcast_text)
    await callback.message.edit_text(
        "✍️ Напиши текст рассылки.\n\n"
        "Поддерживается HTML: <b>жирный</b>, <i>курсив</i>, <code>код</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminState.broadcast_text)
async def process_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    text = message.text or message.caption or ""
    await _do_broadcast(message, bot, text=text)


@router.callback_query(F.data == "adm_bphoto")
async def cb_admin_broadcast_photo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.broadcast_photo)
    await callback.message.edit_text(
        "🖼 Отправь фото для рассылки (с подписью или без).",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminState.broadcast_photo, F.photo)
async def process_broadcast_photo(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    photo_id = message.photo[-1].file_id
    caption = message.caption or ""
    await _do_broadcast(message, bot, photo_id=photo_id, text=caption)


@router.message(AdminState.broadcast_photo)
async def process_broadcast_photo_fallback(message: Message, state: FSMContext):
    await message.answer("❌ Нужно отправить фото.", reply_markup=cancel_kb())


async def _do_broadcast(message: Message, bot: Bot, text: str = "", photo_id: str | None = None):
    user_ids = storage.get_all_user_ids()
    if not user_ids:
        await message.answer("❌ Нет пользователей.", reply_markup=admin_main_kb())
        return

    total = len(user_ids)
    status_msg = await message.answer(f"📤 Рассылка: 0/{total}")
    sent, failed = 0, 0

    for i, uid in enumerate(user_ids, 1):
        if storage.is_banned(uid):
            failed += 1
            continue
        try:
            if photo_id:
                await bot.send_photo(uid, photo=photo_id, caption=text or None, parse_mode="HTML")
            else:
                await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

        if i % 20 == 0:
            try:
                await status_msg.edit_text(f"📤 Рассылка: {i}/{total}…")
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"✉️ Отправлено: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
        reply_markup=admin_back_kb(),
    )


# ─── Banner ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_banner")
async def cb_admin_banner(callback: CallbackQuery):
    banner = storage.get_banner()
    has_banner = bool(banner)
    text = (
        "🖼 <b>Баннер бота</b>\n\n"
        f"Статус: {'✅ Установлен' if has_banner else '❌ Не установлен'}\n\n"
        "Баннер показывается всем пользователям при /start."
    )
    if has_banner:
        try:
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=banner,
                caption=text,
                reply_markup=admin_banner_kb(True),
            )
            await callback.answer()
            return
        except Exception:
            pass
    await callback.message.edit_text(text, reply_markup=admin_banner_kb(False))
    await callback.answer()


@router.callback_query(F.data == "adm_banner_del")
async def cb_admin_banner_del(callback: CallbackQuery):
    storage.set_banner(None)
    await callback.message.edit_text(
        "🗑 Баннер удалён.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer("Удалено")


@router.callback_query(F.data == "adm_banner_set")
async def cb_admin_banner_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.banner_set)
    try:
        await callback.message.edit_text(
            "🖼 Отправь фото для баннера:",
            reply_markup=cancel_kb(),
        )
    except Exception:
        await callback.message.answer("🖼 Отправь фото для баннера:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(AdminState.banner_set, F.photo)
async def process_banner(message: Message, state: FSMContext):
    await state.clear()
    file_id = message.photo[-1].file_id
    storage.set_banner(file_id)
    await message.answer(
        "✅ Баннер установлен! Теперь он будет показываться при /start.",
        reply_markup=admin_main_kb(),
    )


@router.message(AdminState.banner_set)
async def process_banner_fallback(message: Message):
    await message.answer("❌ Нужно отправить фото.", reply_markup=cancel_kb())


# ─── Payments ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_pay:"))
async def cb_admin_payments(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    all_payments = storage.get_payments()
    total = len(all_payments)

    if not all_payments:
        await callback.message.edit_text(
            "💰 <b>Платежи</b>\n\nПлатежей пока нет.",
            reply_markup=admin_back_kb(),
        )
        await callback.answer()
        return

    start = page * PAYMENTS_PER_PAGE
    page_payments = all_payments[start: start + PAYMENTS_PER_PAGE]

    revenue = storage.total_revenue()
    lines = [f"💰 <b>Платежи</b> (всего ${revenue:.2f} USDT)\n"]

    for p in page_payments:
        uid = p.get("user_id")
        u = storage.get_user(uid)
        fn = escape(u.get("first_name", str(uid)))
        plan = SUBSCRIPTION_PLANS.get(p["plan"], {}).get("label", p["plan"])
        amount = p.get("amount", 0)
        paid_at = p.get("paid_at", "")[:16].replace("T", " ")
        lines.append(
            f"• {fn} (<code>{uid}</code>)\n"
            f"  {plan} — <b>${amount}</b> • {paid_at}"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_payments_kb(page, total),
    )
    await callback.answer()


# ─── Settings ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_set")
async def cb_admin_settings(callback: CallbackQuery):
    prices = storage.get_prices()
    free_limit = storage.get_free_limit()
    await callback.message.edit_text(
        "⚙️ <b>Настройки</b>\n\nНажми на параметр чтобы изменить:",
        reply_markup=admin_settings_kb(prices, free_limit),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_price:"))
async def cb_admin_set_price(callback: CallbackQuery, state: FSMContext):
    plan = callback.data.split(":")[1]
    plan_info = SUBSCRIPTION_PLANS.get(plan, {})
    prices = storage.get_prices()
    current = prices.get(plan, plan_info.get("price", "?"))
    await state.set_state(AdminState.set_price)
    await state.update_data(plan=plan)
    await callback.message.edit_text(
        f"⚙️ Цена за <b>{plan_info.get('label', plan)}</b>\n\n"
        f"Текущая: <b>${current} USDT</b>\n\n"
        "Введи новую цену (число, например: <code>5.99</code>):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminState.set_price)
async def process_set_price(message: Message, state: FSMContext):
    data = await state.get_data()
    plan = data.get("plan")
    await state.clear()
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Неверная цена. Введи положительное число.", reply_markup=admin_main_kb())
        return

    storage.set_price(plan, price)
    plan_label = SUBSCRIPTION_PLANS.get(plan, {}).get("label", plan)
    await message.answer(
        f"✅ Цена за <b>{plan_label}</b> установлена: <b>${price} USDT</b>",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "adm_setlim")
async def cb_admin_set_limit(callback: CallbackQuery, state: FSMContext):
    limit = storage.get_free_limit()
    await state.set_state(AdminState.set_limit)
    await callback.message.edit_text(
        f"🆓 Бесплатных анализов в день\n\n"
        f"Текущее значение: <b>{limit}</b>\n\n"
        "Введи новое число (например: <code>3</code>):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminState.set_limit)
async def process_set_limit(message: Message, state: FSMContext):
    await state.clear()
    try:
        limit = int(message.text.strip())
        if limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Неверное число.", reply_markup=admin_main_kb())
        return

    storage.set_free_limit(limit)
    await message.answer(
        f"✅ Лимит бесплатных анализов: <b>{limit}/день</b>",
        reply_markup=admin_main_kb(),
    )


# ─── Admin cancel ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text(
            "👑 <b>Панель администратора</b>\n\nВыбери раздел:",
            reply_markup=admin_main_kb(),
        )
    except Exception:
        await callback.message.answer(
            "👑 <b>Панель администратора</b>\n\nВыбери раздел:",
            reply_markup=admin_main_kb(),
        )
    await callback.answer()
