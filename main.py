import asyncio
import logging
import threading
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "<h1>Telegram Bot is running</h1><p>The bot is active and polling for messages.</p>"

@app.route("/health")
def health():
    return {"status": "ok"}

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

async def run_bot():
    from bot.main import main
    await main()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_bot())
