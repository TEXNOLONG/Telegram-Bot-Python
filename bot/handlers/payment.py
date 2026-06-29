import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery

from bot.config import SUBSCRIPTION_PLANS, SUBSCRIPTION_CURRENCY
from bot.keyboards import payment_kb, subscription_menu_kb, admin_back_kb
from bot.storage import storage
from bot.utils.cryptobot import crypto_api

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("plan:"))
async def cb_select_plan(cb: CallbackQuery):
    plan = cb.data.split(":")[1]
    plan_info = SUBSCRIPTION_PLANS.get(plan)
    if not plan_info:
        await cb.answer("Неверный план", show_alert=True)
        return

    prices = storage.get_prices()
    price = prices.get(plan, plan_info["price"])
    uid = cb.from_user.id

    invoice = await crypto_api.create_invoice(
        asset=SUBSCRIPTION_CURRENCY,
        amount=price,
        description=f"LoadTest Pro — {plan_info['label']}",
        payload=f"{uid}:{plan}",
        expires_in=3600,
    )
    if not invoice:
        await cb.answer("Ошибка создания счёта. Попробуйте позже.", show_alert=True)
        return

    invoice_id = invoice.get("invoice_id")
    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url", "")

    storage.add_pending_invoice(invoice_id, uid, plan)

    await cb.message.answer(
        f"{plan_info['emoji']} <b>{plan_info['label']}</b>\n\n"
        f"💰 Сумма: <b>${price} {SUBSCRIPTION_CURRENCY}</b>\n"
        f"⏳ Счёт действителен: <b>1 час</b>\n\n"
        f"Нажмите «Оплатить», затем «Проверить»:",
        parse_mode=ParseMode.HTML,
        reply_markup=payment_kb(pay_url, invoice_id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("paychk:"))
async def cb_check_payment(cb: CallbackQuery):
    invoice_id = int(cb.data.split(":")[1])
    uid = cb.from_user.id

    pending = storage.get_pending_invoices()
    inv = next((i for i in pending if i["invoice_id"] == invoice_id), None)
    if not inv:
        await cb.answer("Счёт не найден или уже оплачен", show_alert=True)
        return

    inv_data = await crypto_api.get_invoice(invoice_id)
    status = inv_data.get("status", "")

    if status == "paid":
        plan = inv["plan"]
        plan_info = SUBSCRIPTION_PLANS[plan]
        prices = storage.get_prices()
        price = prices.get(plan, plan_info["price"])

        storage.activate_subscription(uid, plan, plan_info["days"])
        storage.remove_pending_invoice(invoice_id)
        storage.add_payment(uid, plan, price, SUBSCRIPTION_CURRENCY)
        expires = storage.sub_expires_str(uid)

        await cb.message.answer(
            f"🎉 <b>Оплата получена!</b>\n\n"
            f"{plan_info['emoji']} Подписка «<b>{plan_info['label']}</b>» активирована\n"
            f"📅 Действует до: <b>{expires}</b>\n\n"
            f"Приятного использования! 🚀",
            parse_mode=ParseMode.HTML,
        )
        await cb.answer("✅ Подписка активирована!", show_alert=True)

    elif status == "expired":
        storage.remove_pending_invoice(invoice_id)
        await cb.answer("⏰ Счёт истёк. Создайте новый.", show_alert=True)

    else:
        await cb.answer("⏳ Оплата ещё не поступила", show_alert=True)
