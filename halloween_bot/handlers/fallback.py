"""Усе, що не впізнали інші роутери (має йти останнім)."""
from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from ..ui.keyboards import main_kb
from .filters import private_router

router = private_router("fallback")
callbacks = Router(name="fallback_callbacks")


@router.message()
async def fallback(m: Message):
    await m.answer("Тисни /start 🎃", reply_markup=main_kb())


@callbacks.callback_query()
async def stale_button(c: CallbackQuery):
    """Кнопка, яку ніхто не обробив (наприклад, крок подачі після перезапуску бота).
    Без відповіді в людини вічно крутився б «годинник»."""
    await c.answer("Ця кнопка застаріла. Натисни /start 🎃", show_alert=True)
