"""Остання лінія захисту: будь-яка неперехоплена помилка в обробнику.

Без цього людина бачила б вічний «годинник» на кнопці або тишу у відповідь,
а помилка лишилась би лише в консолі. Тепер: лог із трасою, ввічлива відповідь людині,
лічильник у /health.
"""
from __future__ import annotations

import logging
import time

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

from ..runtime import rt

log = logging.getLogger("halloween")
router = Router(name="errors")

SORRY = "⚠️ Щось пішло не так. Спробуй ще раз або натисни /start"


def is_stale_query(ex: BaseException) -> bool:
    """Натискання, яке пролежало в Telegram, поки бот був офлайн: дія вже виконана,
    але відповісти спливаючим вікном Telegram не дає. Це не баг."""
    msg = str(ex).lower()
    return isinstance(ex, TelegramBadRequest) and ("query is too old" in msg or "query id is invalid" in msg)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    ex = event.exception
    if is_stale_query(ex):
        log.info("Застаріле натискання (бот був офлайн), дію виконано без спливаючого вікна: %s", ex)
        return True
    log.error("Помилка в обробнику (update %s): %r", event.update.update_id, ex, exc_info=ex)
    rt.errors.append(f"{time.strftime('%H:%M:%S')} {type(ex).__name__}: {ex}"[:300])
    u = event.update
    try:
        if u.callback_query:
            await u.callback_query.answer(SORRY, show_alert=True)
        elif u.message and u.message.chat.type == "private":
            await u.message.answer(SORRY)
    except Exception as ex2:                       # напр., на кнопку вже відповіли до збою
        log.warning("Не вдалося повідомити людину про помилку: %r", ex2)
    return True
