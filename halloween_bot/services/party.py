"""Лише гості вечірки: подавати, голосувати й бути партнером може тільки учасник чату туси.

Як це працює:
* власник (або адмін) додає бота в чат туси — бот запам'ятовує цей чат;
* хто не в чаті, отримує «конкурс лише для гостей», навіть якщо має посилання на бота чи канал;
* посилання на канал стає «за заявкою»: бот сам пускає гостей і відхиляє чужих.

Поки чат туси не підключено, перевірки нема — бот працює як раніше.
"""
from __future__ import annotations

import logging
import time

from aiogram import Bot

from ..domain.people import admin_ids, is_admin
from ..runtime import rt
from ..storage import db, store
from ..ui.texts import esc
from .channel import make_invite_link
from .membership import member_status_ok
from .messaging import safe_send

log = logging.getLogger("halloween.party")

POSITIVE_TTL = 600       # с: гостя перепитуємо раз на 10 хв (раптом вийшов, а події ми не бачили)
NEGATIVE_TTL = 5         # с: не-гостя — частіше: його могли щойно додати в чат

NOT_GUEST = ("🔒 Конкурс лише для гостей вечірки.\n"
             "Тебе нема в чаті туси. Якщо ти записаний(-а), попроси оргів додати тебе в чат "
             "і спробуй ще раз.")
CANT_CHECK = "⚠️ Не вдалося перевірити, чи ти в чаті туси. Спробуй ще раз за хвилину."


async def is_guest(bot: Bot, uid: int) -> bool | None:
    """True/False — чи є людина в чаті туси; None — Telegram не відповів."""
    cached = rt.party_cache.get(uid)
    if cached:
        ok, at = cached
        if time.time() - at < (POSITIVE_TTL if ok else NEGATIVE_TTL):
            return ok
    try:
        cm = await bot.get_chat_member(db["party_chat_id"], uid)
    except Exception as ex:
        log.warning("get_chat_member(чат туси, %s) failed: %s", uid, ex)
        return None
    ok = member_status_ok(cm)
    rt.party_cache[uid] = (ok, time.time())
    return ok


async def party_gate(bot: Bot, user) -> str | None:
    """None — можна брати участь; інакше текст відмови (≤200 символів, влазить у спливаюче вікно)."""
    if not db["party_chat_id"] or is_admin(user.id):
        return None
    ok = await is_guest(bot, user.id)
    if ok is None:
        return CANT_CHECK
    return None if ok else NOT_GUEST


def apply_party_event(chat_id: int, cm) -> bool:
    """Хтось вступив у чат туси / вийшов. Потрібно, щоб бот був адміном чату (інакше подій нема)."""
    if not db["party_chat_id"] or chat_id != db["party_chat_id"]:
        return False
    rt.party_cache[cm.user.id] = (member_status_ok(cm), time.time())
    return True


async def switch_channel_to_requests(bot: Bot) -> str:
    """Посилання на канал → «за заявкою»: переслане чужим, воно нічого не дасть.
    Старе відкрите посилання, яке створив бот, анулюємо. Повертає текст для адмінів."""
    if not db["channel_id"] or db.get("channel_link_mode") == "request":
        return ""
    new, new_mode = await make_invite_link(bot, db["channel_id"], request=True)
    if new_mode != "request":
        return ("\n⚠️ Не вдалося зробити посилання на канал «за заявкою» (бракує права «Запрошувати»?). "
                "Поки що посилання на канал лишається відкритим.")
    old, mode = db["channel_link"], db.get("channel_link_mode")
    db["channel_link"], db["channel_link_mode"] = new, "request"
    store.save()
    if old and mode == "open":
        try:
            await bot.revoke_chat_invite_link(db["channel_id"], old)
        except Exception as ex:
            log.warning("Не вдалося анулювати старе посилання на канал: %s", ex)
    return "\n🔐 Посилання на канал тепер «за заявкою»: гостей туси я пускаю сам, чужих — ні."


async def on_party_bot_status(bot: Bot, chat, actor, new_member) -> None:
    """Бота додали в груповий чат / зробили там адміном / прибрали."""
    title = esc(chat.title or "")
    if new_member.status in ("member", "administrator"):
        if not is_admin(actor.id):
            log.warning("Бота додав у чат «%s» не-адмін %s, ігнорую", chat.title, actor.id)
            return
        changed = db["party_chat_id"] != chat.id
        db["party_chat_id"], db["party_title"] = chat.id, chat.title or ""
        rt.party_cache.clear()
        store.save()
        log.info("Чат туси: %s (%s), статус бота %s", chat.title, chat.id, new_member.status)
        text = (f"🎟 Чат туси «{title}» підключено! Подавати, голосувати й бути партнером "
                "тепер можуть лише його учасники (адміни — завжди)." if changed
                else f"🎟 Чат туси «{title}»: права бота оновлено.")
        if new_member.status != "administrator":
            text += ("\n⚠️ Зроби бота <b>адміном</b> чату (жодних прав не треба): тоді він одразу бачить, "
                     "хто вийшов з чату. Без цього перевіряю раз на 10 хв.")
        if changed:
            text += await switch_channel_to_requests(bot)
        for a in admin_ids():
            await safe_send(bot, a, text)
    elif chat.id == db["party_chat_id"] and new_member.status in ("left", "kicked"):
        db["party_chat_id"], db["party_title"] = None, None
        rt.party_cache.clear()
        store.save()
        log.warning("Бота прибрали з чату туси «%s»: перевірку гостей вимкнено", chat.title)
        for a in admin_ids():
            await safe_send(bot, a, f"⚠️ Бота прибрали з чату туси «{title}». Перевірку гостей "
                                    "<b>вимкнено</b>: голосувати може будь-хто з каналу. "
                                    "Додай бота назад, щоб увімкнути.")


async def on_join_request(bot: Bot, req) -> None:
    """Заявка на вступ у канал: гостей туси й адмінів пускаємо, чужих відхиляємо."""
    if req.chat.id != db["channel_id"]:
        return
    uid = req.from_user.id
    if not db["party_chat_id"] or is_admin(uid):
        ok = True
    else:
        ok = await is_guest(bot, uid)
    if ok is None:
        log.warning("Заявка в канал від %s: не вдалося перевірити, лишаю на розгляд адмінів", uid)
        return
    try:
        if ok:
            await bot.approve_chat_join_request(req.chat.id, uid)
        else:
            await bot.decline_chat_join_request(req.chat.id, uid)
    except Exception as ex:                       # заявку вже обробив адмін руками
        log.warning("Заявка в канал від %s: %s", uid, ex)
        return
    log.info("Заявка в канал від %s: %s", uid, "прийнято" if ok else "відхилено (не в чаті туси)")
    if not ok:
        await safe_send(bot, req.user_chat_id or uid, NOT_GUEST)
