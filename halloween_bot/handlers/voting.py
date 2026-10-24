"""Кнопка «🗳 Голосувати» під постами в каналі."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from ..services.voting import cast_vote

router = Router(name="voting")


@router.callback_query(F.data.regexp(r"^v:\d+$"))
async def vote(c: CallbackQuery, bot: Bot):
    text = await cast_vote(bot, c.from_user, int(c.data[2:]))
    await c.answer(text, show_alert=True)
