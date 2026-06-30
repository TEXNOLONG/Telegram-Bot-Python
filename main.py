import asyncio
import logging
import signal
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_shutdown_event = asyncio.Event() if False else None  # set properly in run_bot


def run_flask():
    from flask_app import app
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


async def run_bot():
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    from aiogram import Bot, Dispatcher
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties

    from bot.config import BOT_TOKEN, SUBSCRIPTION_PLANS, SUBSCRIPTION_CURRENCY
    from bot.db import init_db
    from bot.storage import storage
    from bot.utils.cryptobot import crypto_api
    from bot.utils.traffic_worker import task_queue_worker, scheduled_task_worker
    from bot.handlers import admin, payment, user

    init_db()
    logger.info("Database initialised")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(payment.router)
    dp.include_router(user.router)

    # ── Graceful shutdown on SIGTERM / SIGINT ─────────────────────────────
    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("Shutdown signal received — stopping bot gracefully...")
        _shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    logger.info("Бот запущен")

    async def payment_poller():
        logger.info("Payment poller started")
        while not _shutdown_event.is_set():
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
                            )
                        except Exception as e:
                            logger.warning("Failed to notify user %s: %s", uid, e)
                    elif status == "expired":
                        storage.remove_pending_invoice(invoice["invoice_id"])
                except Exception as e:
                    logger.error("Payment poller error: %s", e)

    async def _shutdown_watcher():
        await _shutdown_event.wait()
        logger.info("Stopping polling...")
        await dp.stop_polling()

    try:
        await asyncio.gather(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            payment_poller(),
            task_queue_worker(bot=bot),
            scheduled_task_worker(bot=bot),
            _shutdown_watcher(),
        )
    finally:
        logger.info("Closing bot session...")
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started on port 5000")

    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process exited.")
