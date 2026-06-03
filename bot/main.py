import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from bot.config import BOT_TOKEN, SUBSCRIPTION_PLANS, SUBSCRIPTION_CURRENCY
from bot.handlers import admin, payment, user
from bot.storage import storage
from bot.utils.cryptobot import crypto_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def payment_poller(bot: Bot):
    logger.info("Payment poller started")
    while True:
        await asyncio.sleep(30)
        pending = storage.get_pending_invoices()
        if not pending:
            continue

        for invoice in list(pending):
            try:
                inv_data = await crypto_api.get_invoice(invoice["invoice_id"])
                status = inv_data.get("status", "")

                if status == "paid":
                    plan = invoice["plan"]
                    uid = invoice["user_id"]
                    plan_info = SUBSCRIPTION_PLANS[plan]
                    prices = storage.get_prices()
                    price = prices.get(plan, plan_info["price"])

                    storage.activate_subscription(uid, plan, plan_info["days"])
                    storage.remove_pending_invoice(invoice["invoice_id"])
                    storage.add_payment(uid, plan, price, SUBSCRIPTION_CURRENCY)

                    expires = storage.sub_expires_str(uid)
                    try:
                        await bot.send_message(
                            uid,
                            f"🎉 <b>Оплата получена!</b>\n\n"
                            f"{plan_info['emoji']} Подписка «<b>{plan_info['label']}</b>» активирована\n"
                            f"📅 Действует до: <b>{expires}</b>\n\n"
                            "Приятного использования! 🚀",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception as e:
                        logger.warning("Failed to notify user %s: %s", uid, e)

                    logger.info("Subscription activated for user %s, plan %s", uid, plan)

                elif status == "expired":
                    storage.remove_pending_invoice(invoice["invoice_id"])
                    logger.info("Invoice %s expired", invoice["invoice_id"])

            except Exception as e:
                logger.error("Payment poller error for invoice %s: %s", invoice.get("invoice_id"), e)


async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(admin.router)
    dp.include_router(payment.router)
    dp.include_router(user.router)

    logger.info("Бот запущен")

    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        payment_poller(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
