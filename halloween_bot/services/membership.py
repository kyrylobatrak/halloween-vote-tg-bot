"""Хто підписаний на канал. Бот веде список сам з подій «вступив/вийшов»
і питає Telegram лише про тих, кого ще не бачив."""
from __future__ import annotations

import logging
import time

from aiogram import Bot

from ..config import settings
from ..runtime import rt
from ..storage import db, store

log = logging.getLogger("halloween.membership")

NEGATIVE_RECHECK = 5     # с: не-підписника не перепитуємо частіше (на випадок пропущеної події)


def member_status_ok(cm) -> bool:
    return cm.status in ("creator", "administrator", "member") or (
        cm.status == "restricted" and getattr(cm, "is_member", False))


async def is_channel_member(bot: Bot, uid: int) -> bool | None:
    """True/False, або None, якщо Telegram не відповів."""
    if not settings.require_member or not db["channel_id"]:
        return True
    known = db["members"].get(str(uid))
    if known is True:
        return True
    if known is False and time.time() - rt.member_checked.get(uid, 0) < NEGATIVE_RECHECK:
        return False
    try:
        cm = await bot.get_chat_member(db["channel_id"], uid)
    except Exception as ex:
        log.warning("get_chat_member %s failed: %s", uid, ex)
        return None
    ok = member_status_ok(cm)
    db["members"][str(uid)] = ok
    rt.member_checked[uid] = time.time()
    store.save()
    return ok


def apply_member_event(chat_id: int, cm) -> bool:
    """Подія каналу «вступив/вийшов». True, якщо це наш канал і список оновлено."""
    if chat_id != db["channel_id"]:
        return False
    uid = cm.user.id
    db["members"][str(uid)] = member_status_ok(cm)
    rt.member_checked[uid] = time.time()        # подія = свіжа перевірка, Telegram не перепитуємо
    store.save()
    return True
