import aiohttp
import logging
from bot.config import CRYPTO_BOT_TOKEN, CRYPTO_API_URL

logger = logging.getLogger(__name__)


class CryptoBotAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Crypto-Pay-API-Token": token}

    async def _get(self, method: str, params: dict = None) -> dict:
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(
                    f"{CRYPTO_API_URL}/{method}",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return data.get("result", {})
                    logger.warning("CryptoBot error: %s", data.get("error"))
        except Exception as e:
            logger.error("CryptoBot GET %s error: %s", method, e)
        return {}

    async def _post(self, method: str, payload: dict = None) -> dict:
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(
                    f"{CRYPTO_API_URL}/{method}",
                    json=payload or {},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        return data.get("result", {})
                    logger.warning("CryptoBot error: %s", data.get("error"))
        except Exception as e:
            logger.error("CryptoBot POST %s error: %s", method, e)
        return {}

    async def get_me(self) -> dict:
        return await self._get("getMe")

    async def get_balance(self) -> list:
        result = await self._get("getBalance")
        return result if isinstance(result, list) else []

    async def create_invoice(
        self,
        asset: str,
        amount: float,
        description: str,
        payload: str,
        expires_in: int = 3600,
    ) -> dict:
        return await self._post("createInvoice", {
            "currency_type": "crypto",
            "asset": asset,
            "amount": str(amount),
            "description": description,
            "payload": payload,
            "expires_in": expires_in,
        })

    async def get_invoice(self, invoice_id: int) -> dict:
        result = await self._get("getInvoices", {"invoice_ids": str(invoice_id)})
        items = result.get("items", []) if isinstance(result, dict) else []
        return items[0] if items else {}


crypto_api = CryptoBotAPI(CRYPTO_BOT_TOKEN)
