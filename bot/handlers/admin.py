import logging
from datetime import datetime

from aiogram import F, Router, Bot
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.filters.base import Filter

from bot.config import ADMIN_ID, SUBSCRIPTION_PLANS, USERS_PER_PAGE, PAYMENTS_PER_PAGE, DOMAIN
from bot.keyboards import (
    admin_main_kb, admin_back_kb, admin_users_kb, admin_user_kb,
    admin_give_sub_kb, admin_broadcast_kb, admin_banner_kb,
    admin_settings_kb, admin_payments_kb, cancel_kb,
)
from bot.storage import storage
from bot.utils.url_validator import validate_target_url
from bot.utils.traffic_worker import enqueue_task

logger = logging.getLogger(__name__)
router = Router()


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        user_id = getattr(getattr(event, "from_user", None), "id", None)
        return user_id == ADMIN_ID


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminStates(StatesGroup):
    broadcast_text = State()
    broadcast_photo = State()
    broadcast_caption = State()
    ban_id = State()
    set_price = State()
    set_price_value = State()
    set_limit = State()
    banner_set = State()
    custom_test_url = State()
    custom_test_params = State()


# ─── /admin ───────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    stats = _get_stats()
    text = (
        f"🛡 <b>Панель администратора</b>\n\n"
        f"👥 Пользователей: <b>{stats['total']}</b>\n"
        f"💎 PRO-подписчиков: <b>{stats['subs']}</b>\n"
        f"📊 Тестов сегодня: <b>{stats['new_today']}</b>\n"
        f"💰 Выручка: <b>${stats['revenue']:.2f}</b>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_kb())
    storage.log_admin_action(message.from_user.id, "Открыл панель администратора")


@router.callback_query(F.data == "adm")
async def cb_adm(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    stats = _get_stats()
    text = (
        f"🛡 <b>Панель администратора</b>\n\n"
        f"👥 Пользователей: <b>{stats['total']}</b>\n"
        f"💎 PRO-подписчиков: <b>{stats['subs']}</b>\n"
        f"📊 Новых сегодня: <b>{stats['new_today']}</b>\n"
        f"💰 Выручка: <b>${stats['revenue']:.2f}</b>"
    )
    await cb.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_kb())
    await cb.answer()


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_stats")
async def cb_stats(cb: CallbackQuery):
    stats = _get_stats()
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total']}</b>\n"
        f"🆕 Новых сегодня: <b>{stats['new_today']}</b>\n"
        f"💎 PRO-подписчиков: <b>{stats['subs']}</b>\n"
        f"🚫 Забанено: <b>{stats['banned']}</b>\n"
        f"📈 Всего анализов: <b>{stats['analyses']}</b>\n"
        f"💰 Выручка: <b>${stats['revenue']:.2f} USDT</b>"
    )
    await cb.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await cb.answer()


def _get_stats() -> dict:
    return {
        "total": storage.total_users(),
        "new_today": storage.new_users_today(),
        "subs": storage.subscribed_count(),
        "banned": storage.banned_count(),
        "analyses": storage.total_analyses(),
        "revenue": storage.total_revenue(),
    }


# ─── Users ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_users:"))
async def cb_users(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    all_users = storage.get_all_users_list()
    total = len(all_users)
    chunk = all_users[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]
    text = f"👥 <b>Пользователи</b> ({total})\nСтраница {page + 1}/{max(1,(total-1)//USERS_PER_PAGE+1)}:"
    await cb.message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=admin_users_kb(chunk, page, total)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm_u:"))
async def cb_user_detail(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    u = storage.get_user(uid)
    if not u:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    reg_status = "✅" if u.get("web_registered") else "❌"
    sub_status = "💎 PRO" if storage.is_pro(uid) else "🔧 LITE"
    expires = storage.sub_expires_str(uid) or "—"
    ban = "🚫 Забанен" if u.get("banned") else "✅ Активен"
    ip = u.get("ip_address") or "не зарегистрирован"

    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"Имя: <b>{u.get('first_name','—')}</b>\n"
        f"Username: {('@'+u['username']) if u.get('username') else '—'}\n"
        f"Регистрация: {reg_status}\n"
        f"Тариф: {sub_status}\n"
        f"Подписка до: <b>{expires}</b>\n"
        f"Статус: {ban}\n"
        f"Проверок: <b>{u.get('total_analyses',0)}</b>\n"
        f"Последняя активность: {(u.get('last_active') or '')[:16]}"
    )
    is_banned = bool(u.get("banned"))
    has_sub = storage.is_pro(uid)
    await cb.message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=admin_user_kb(uid, is_banned, has_sub)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm_ban:"))
async def cb_ban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    storage.ban_user(uid)
    storage.log_admin_action(cb.from_user.id, f"Забанил пользователя {uid}")
    await cb.answer("🚫 Пользователь заблокирован", show_alert=True)
    u = storage.get_user(uid)
    if u:
        await cb.message.edit_reply_markup(
            reply_markup=admin_user_kb(uid, True, storage.is_pro(uid))
        )


@router.callback_query(F.data.startswith("adm_uban:"))
async def cb_unban(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    storage.unban_user(uid)
    storage.log_admin_action(cb.from_user.id, f"Разбанил пользователя {uid}")
    await cb.answer("✅ Пользователь разблокирован", show_alert=True)
    u = storage.get_user(uid)
    if u:
        await cb.message.edit_reply_markup(
            reply_markup=admin_user_kb(uid, False, storage.is_pro(uid))
        )


# ─── Ban by ID ────────────────────────────────────────────────────────────────

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /ban <user_id>")
        return
    try:
        uid = int(parts[1])
        storage.ban_user(uid)
        storage.log_admin_action(message.from_user.id, f"Забанил пользователя {uid}")
        await message.answer(f"🚫 Пользователь {uid} заблокирован.")
    except ValueError:
        await message.answer("❌ Неверный ID")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /unban <user_id>")
        return
    try:
        uid = int(parts[1])
        storage.unban_user(uid)
        storage.log_admin_action(message.from_user.id, f"Разбанил пользователя {uid}")
        await message.answer(f"✅ Пользователь {uid} разблокирован.")
    except ValueError:
        await message.answer("❌ Неверный ID")


# ─── Give subscription ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_gs:"))
async def cb_give_sub(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    await cb.message.answer(f"💎 Выберите план для пользователя {uid}:",
                            reply_markup=admin_give_sub_kb(uid))
    await cb.answer()


@router.callback_query(F.data.startswith("adm_gsp:"))
async def cb_give_sub_plan(cb: CallbackQuery):
    _, uid_str, plan = cb.data.split(":")
    uid = int(uid_str)
    plan_info = SUBSCRIPTION_PLANS.get(plan)
    if not plan_info:
        await cb.answer("Неверный план", show_alert=True)
        return
    storage.manually_grant_subscription(uid, plan, plan_info["days"])
    storage.log_admin_action(
        cb.from_user.id,
        f"Выдал подписку {plan} ({plan_info['days']} дней) пользователю {uid}"
    )
    await cb.answer(f"✅ {plan_info['emoji']} {plan_info['label']} выдана пользователю {uid}", show_alert=True)


# ─── Payments ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_pay:"))
async def cb_payments(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    payments = storage.get_payments()
    total = len(payments)
    chunk = payments[page * PAYMENTS_PER_PAGE:(page + 1) * PAYMENTS_PER_PAGE]
    lines = [f"💰 <b>Платежи</b> ({total})\n"]
    for p in chunk:
        lines.append(
            f"• #{p['user_id']} {p['plan']} — ${p['amount']} {p['currency']} "
            f"<i>{(p.get('paid_at',''))[:10]}</i>"
        )
    if not chunk:
        lines.append("Платежей нет")
    await cb.message.answer(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=admin_payments_kb(page, total)
    )
    await cb.answer()


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_bcast")
async def cb_broadcast(cb: CallbackQuery):
    await cb.message.answer("📢 Выберите тип рассылки:", reply_markup=admin_broadcast_kb())
    await cb.answer()


@router.callback_query(F.data == "adm_btext")
async def cb_btext(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.broadcast_text)
    await cb.message.answer("Введите текст рассылки:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(AdminStates.broadcast_text)
async def handle_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    import asyncio
    await state.clear()
    uids = storage.get_all_user_ids()
    text = message.text
    ok = fail = 0
    for uid in uids:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    storage.log_admin_action(message.from_user.id, f"Рассылка: {ok} успешно, {fail} ошибок")
    await message.answer(f"📢 Рассылка: ✅ {ok} / ❌ {fail}", reply_markup=admin_back_kb())


@router.callback_query(F.data == "adm_bphoto")
async def cb_bphoto(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.broadcast_photo)
    await cb.message.answer("Отправьте фото для рассылки:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(AdminStates.broadcast_photo, F.photo)
async def handle_broadcast_photo(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await state.set_state(AdminStates.broadcast_caption)
    await message.answer("Теперь введите подпись (или /skip):")


@router.message(AdminStates.broadcast_caption)
async def handle_broadcast_caption(message: Message, state: FSMContext, bot: Bot):
    import asyncio
    data = await state.get_data()
    await state.clear()
    photo_id = data.get("photo_id")
    caption = message.text if message.text != "/skip" else ""
    uids = storage.get_all_user_ids()
    ok = fail = 0
    for uid in uids:
        try:
            await bot.send_photo(uid, photo=photo_id, caption=caption, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    storage.log_admin_action(message.from_user.id, f"Фото-рассылка: {ok}/{fail}")
    await message.answer(f"📢 Рассылка: ✅ {ok} / ❌ {fail}", reply_markup=admin_back_kb())


# ─── Banner ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_banner")
async def cb_banner(cb: CallbackQuery):
    has_banner = bool(storage.get_banner())
    await cb.message.answer("🖼 Управление баннером:", reply_markup=admin_banner_kb(has_banner))
    await cb.answer()


@router.callback_query(F.data == "adm_banner_del")
async def cb_banner_del(cb: CallbackQuery):
    storage.set_banner(None)
    storage.log_admin_action(cb.from_user.id, "Удалил баннер")
    await cb.answer("🗑 Баннер удалён", show_alert=True)


@router.callback_query(F.data == "adm_banner_set")
async def cb_banner_set(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.banner_set)
    await cb.message.answer("Отправьте фото для баннера:", reply_markup=cancel_kb())
    await cb.answer()


@router.message(AdminStates.banner_set, F.photo)
async def handle_banner_photo(message: Message, state: FSMContext):
    await state.clear()
    storage.set_banner(message.photo[-1].file_id)
    storage.log_admin_action(message.from_user.id, "Установил баннер")
    await message.answer("✅ Баннер установлен!", reply_markup=admin_back_kb())


# ─── Settings ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_set")
async def cb_settings(cb: CallbackQuery):
    prices = storage.get_prices()
    limit = storage.get_free_limit()
    await cb.message.answer("⚙️ Настройки:", parse_mode=ParseMode.HTML,
                            reply_markup=admin_settings_kb(prices, limit))
    await cb.answer()


@router.callback_query(F.data.startswith("adm_price:"))
async def cb_set_price(cb: CallbackQuery, state: FSMContext):
    plan = cb.data.split(":")[1]
    await state.set_state(AdminStates.set_price)
    await state.update_data(plan=plan)
    prices = storage.get_prices()
    current = prices.get(plan, "?")
    await cb.message.answer(
        f"Текущая цена для <b>{plan}</b>: <b>${current}</b>\nВведите новую цену (USD):",
        parse_mode=ParseMode.HTML, reply_markup=cancel_kb()
    )
    await cb.answer()


@router.message(AdminStates.set_price)
async def handle_set_price(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    plan = data.get("plan")
    try:
        price = float(message.text.strip())
        storage.set_price(plan, price)
        storage.log_admin_action(message.from_user.id, f"Установил цену {plan}=${price}")
        await message.answer(f"✅ Цена для <b>{plan}</b> установлена: <b>${price}</b>",
                             parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    except ValueError:
        await message.answer("❌ Введите числовое значение", reply_markup=cancel_kb())


@router.callback_query(F.data == "adm_setlim")
async def cb_set_limit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.set_limit)
    current = storage.get_free_limit()
    await cb.message.answer(
        f"Текущий лимит: <b>{current}</b>/день\nВведите новое значение:",
        parse_mode=ParseMode.HTML, reply_markup=cancel_kb()
    )
    await cb.answer()


@router.message(AdminStates.set_limit)
async def handle_set_limit(message: Message, state: FSMContext):
    await state.clear()
    try:
        limit = int(message.text.strip())
        storage.set_free_limit(limit)
        storage.log_admin_action(message.from_user.id, f"Установил бесплатный лимит: {limit}")
        await message.answer(f"✅ Лимит установлен: <b>{limit}</b>/день",
                             parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    except ValueError:
        await message.answer("❌ Введите целое число", reply_markup=cancel_kb())


# ─── Admin logs ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_logs")
async def cb_logs(cb: CallbackQuery):
    logs = storage.get_admin_logs(50)
    if not logs:
        await cb.answer("Логов нет", show_alert=True)
        return
    lines = ["📋 <b>Последние действия</b>\n"]
    for l in logs:
        lines.append(f"<i>{l['timestamp']}</i> — {l['action']}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await cb.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await cb.answer()


@router.message(Command("logs"))
async def cmd_logs(message: Message):
    logs = storage.get_admin_logs(50)
    lines = ["📋 <b>Последние 50 действий</b>\n"]
    for l in logs:
        lines.append(f"<i>{l['timestamp']}</i> — {l['action']}")
    text = "\n".join(lines) if logs else "Логов нет"
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await message.answer(text, parse_mode=ParseMode.HTML)


# ─── /stats command ───────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    stats = _get_stats()
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{stats['total']}</b>\n"
        f"🆕 Новых сегодня: <b>{stats['new_today']}</b>\n"
        f"💎 PRO: <b>{stats['subs']}</b>\n"
        f"🚫 Забанено: <b>{stats['banned']}</b>\n"
        f"📊 Всего анализов: <b>{stats['analyses']}</b>\n"
        f"💰 Выручка: <b>${stats['revenue']:.2f} USDT</b>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


# ─── /broadcast command ───────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    text = message.text[len("/broadcast"):].strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    uids = storage.get_all_user_ids()
    ok = fail = 0
    for uid in uids:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
    storage.log_admin_action(message.from_user.id, f"Рассылка командой: {ok}/{fail}")
    await message.answer(f"📢 ✅ {ok} / ❌ {fail}")


# ─── Admin custom test (bypass limits) ───────────────────────────────────────

@router.callback_query(F.data == "adm_test")
async def cb_admin_test(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.custom_test_url)
    await cb.message.answer(
        "🚀 <b>Кастомный нагрузочный тест (admin)</b>\n\n"
        "Отправьте URL цели:",
        parse_mode=ParseMode.HTML, reply_markup=cancel_kb()
    )
    await cb.answer()


@router.message(AdminStates.custom_test_url)
async def handle_admin_test_url(message: Message, state: FSMContext):
    url = message.text.strip()
    ok, url_or_err = validate_target_url(url)
    if not ok:
        await message.answer(f"❌ {url_or_err}", reply_markup=cancel_kb())
        return
    await state.update_data(url=url_or_err)
    await state.set_state(AdminStates.custom_test_params)
    await message.answer(
        "Введите параметры в формате:\n"
        "<code>rps duration intensity</code>\n\n"
        "Пример: <code>500 120 medium</code>\n"
        "Intensity: low / medium / high / ultra",
        parse_mode=ParseMode.HTML, reply_markup=cancel_kb()
    )


@router.message(AdminStates.custom_test_params)
async def handle_admin_test_params(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    parts = message.text.strip().split()
    try:
        rps = int(parts[0]) if len(parts) > 0 else 500
        duration = int(parts[1]) if len(parts) > 1 else 60
        intensity = parts[2] if len(parts) > 2 else "medium"
    except (ValueError, IndexError):
        rps, duration, intensity = 500, 60, "medium"

    url = data.get("url", "")
    uid = message.from_user.id

    task_id = enqueue_task(uid, "load_test", {
        "target_url": url,
        "mode": "pro",
        "duration": duration,
        "intensity": intensity,
        "admin_override": True,
    })

    storage.log_admin_action(uid, f"Запустил кастомный тест: {url} {rps}RPS {duration}s {intensity}")
    await message.answer(
        f"🚀 <b>Тест запущен (admin)</b>\n\n"
        f"🌐 URL: <code>{url}</code>\n"
        f"⚡ RPS: {rps} | ⏱ {duration}с | 📊 {intensity}\n\n"
        f"Отчёт придёт автоматически.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
