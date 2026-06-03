from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """Edit text or caption of a message. Falls back to delete+send if message is a photo."""
    msg = callback.message
    try:
        if msg.photo or msg.document or msg.video or msg.animation:
            await msg.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
