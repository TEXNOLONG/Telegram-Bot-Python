import os

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_ID: int = int(os.environ["ADMIN_ID"])
CHANNEL_USERNAME: str = "hayder_projectx"
CHANNEL_LINK: str = "https://t.me/hayder_projectx"
CRYPTO_BOT_TOKEN: str = os.getenv("CRYPTO_BOT_TOKEN", "")
CRYPTO_API_URL: str = "https://pay.crypt.bot/api"
DOMAIN: str = os.getenv("REPLIT_DEV_DOMAIN", "localhost:5000")

SUBSCRIPTION_PLANS: dict = {
    "week":    {"days": 7,  "price": 2.99,  "label": "PRO 7 дней",  "emoji": "⚡"},
    "month":   {"days": 30, "price": 7.99,  "label": "PRO 30 дней", "emoji": "💎"},
    "quarter": {"days": 90, "price": 19.99, "label": "PRO 90 дней", "emoji": "👑"},
}
SUBSCRIPTION_CURRENCY: str = "USDT"
FREE_ANALYSES_PER_DAY: int = 3
USERS_PER_PAGE: int = 8
PAYMENTS_PER_PAGE: int = 10
