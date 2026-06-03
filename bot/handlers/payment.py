import logging
from html import escape
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.config import SUBSCRIPTION_PLANS, SUBSCRIPTION_CURRENCY
from bot.keyboards import subscription_menu_kb, payment_kb, main_menu_kb
from bot.storage import storage
from bot.utils.cryptobot import crypto_api

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "sub")
async def cb_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    prices = storage.get_prices()
    has_sub = storage.has_active_sub(user_id)
    expires = storage.sub_expires_str(user_id)

    if has_sub:
        text = (
            f"💎 <b>Твоя подписка активна</b>\n\n"
            f"📅 Действует до: <b>{expires}</b>\n\n"
            "Выбери план для продления подписки:"
        )
    else:
        limit = storage.get_free_limit()
        free_left = storage.free_left(user_id)
        text = (
            "💎 <b>Подписка — безлимитный анализ сайтов</b>\n\n"
            f"🆓 Бесплатно: <b>{free_left}/{limit}</b> анализов сегодня\n\n"
            "<b>Планы подписки (оплата в USDT):</b>"
        )

    await callback.message.edit_text(
        text,
        reply_markup=subscription_menu_kb(prices, has_sub, expires),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def cb_select_plan(callback: CallbackQuery):
    plan = callback.data.split(":")[1]
    if plan not in SUBSCRIPTION_PLANS:
        await callback.answer("❌ Неверный план", show_alert=True)
        return

    user_id = callback.from_user.id
    plan_info = SUBSCRIPTION_PLANS[plan]
    prices = storage.get_prices()
    price = prices.get(plan, plan_info["price"])

    await callback.answer("⏳ Создаю счёт...")

    invoice = await crypto_api.create_invoice(
        asset=SUBSCRIPTION_CURRENCY,
        amount=price,
        description=f"Подписка {plan_info['label']} — Site Analyzer Bot",
        payload=f"{user_id}:{plan}",
        expires_in=3600,
    )

    if not invoice:
        await callback.message.edit_text(
            "❌ <b>Не удалось создать счёт.</b>\n\n"
            "Проверь настройки CryptoBot или попробуй позже.",
            reply_markup=main_menu_kb(storage.has_active_sub(user_id)),
        )
        return

    invoice_id = invoice.get("invoice_id")
    pay_url = invoice.get("pay_url", "")

    storage.add_pending_invoice(invoice_id, user_id, plan)

    expires_at = datetime.now() + timedelta(hours=1)
    await callback.message.edit_text(
        f"{plan_info['emoji']} <b>Счёт создан</b>\n\n"
        f"📦 План: <b>{plan_info['label']}</b>\n"
        f"💰 Сумма: <b>${price} {SUBSCRIPTION_CURRENCY}</b>\n"
        f"⏰ Действует до: {expires_at.strftime('%H:%M')}\n\n"
        "Нажми кнопку ниже для оплаты через CryptoBot.\n"
        "После оплаты нажми <b>«✅ Проверить оплату»</b>",
        reply_markup=payment_kb(pay_url, invoice_id),
    )


@router.callback_query(F.data.startswith("paychk:"))
async def cb_check_payment(callback: CallbackQuery):
    invoice_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    await callback.answer("🔍 Проверяю оплату...")

    pending = storage.get_pending_invoices()
    this_invoice = next((inv for inv in pending if inv["invoice_id"] == invoice_id), None)

    if not this_invoice:
        if storage.has_active_sub(user_id):
            await callback.message.edit_text(
                "✅ <b>Подписка уже активна!</b>\n\n"
                f"📅 Действует до: <b>{storage.sub_expires_str(user_id)}</b>",
                reply_markup=main_menu_kb(True),
            )
        else:
            await callback.message.edit_text(
                "❌ Счёт не найден или уже истёк.",
                reply_markup=main_menu_kb(False),
            )
        return

    inv_data = await crypto_api.get_invoice(invoice_id)
    status = inv_data.get("status", "")

    if status == "paid":
        plan = this_invoice["plan"]
        plan_info = SUBSCRIPTION_PLANS[plan]
        prices = storage.get_prices()
        price = prices.get(plan, plan_info["price"])

        storage.activate_subscription(user_id, plan, plan_info["days"])
        storage.remove_pending_invoice(invoice_id)
        storage.add_payment(user_id, plan, price, SUBSCRIPTION_CURRENCY)

        expires = storage.sub_expires_str(user_id)
        await callback.message.edit_text(
            f"🎉 <b>Оплата подтверждена!</b>\n\n"
            f"{plan_info['emoji']} Подписка «<b>{plan_info['label']}</b>» активирована\n"
            f"📅 Действует до: <b>{expires}</b>\n\n"
            "Теперь у тебя безлимитный анализ сайтов! 🚀",
            reply_markup=main_menu_kb(True),
        )
    elif status == "expired":
        storage.remove_pending_invoice(invoice_id)
        await callback.message.edit_text(
            "⏰ <b>Счёт истёк.</b>\n\nСоздай новый счёт для оплаты.",
            reply_markup=main_menu_kb(storage.has_active_sub(user_id)),
        )
    else:
        await callback.answer(
            "⏳ Оплата ещё не поступила. Попробуй через несколько секунд.",
            show_alert=True,
        )
