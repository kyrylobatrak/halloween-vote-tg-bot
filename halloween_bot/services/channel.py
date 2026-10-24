"""Підключення каналу й посилання-запрошення."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from ..domain.people import admin_ids, is_admin
from ..storage import db, store
from ..ui.texts import esc
from .messaging import safe_send

log = logging.getLogger("halloween.channel")

INVITE_ATTEMPTS = 4
INVITE_RETRY_DELAY = 2.0     # с між спробами створити посилання

RIGHTS = (("can_post_messages", "публікувати"),
          ("can_edit_messages", "редагувати"),
          ("can_delete_messages", "видаляти"),
          ("can_invite_users", "запрошувати"))


def missing_rights(member) -> list[str]:
    return [name for flag, name in RIGHTS if getattr(member, flag, None) is False]


async def make_invite_link(bot: Bot, chat_id: int, request: bool | None = None) -> tuple[str | None, str | None]:
    """(посилання, режим). Окреме (не основне) посилання: createChatInviteLink, на відміну від
    exportChatInviteLink, НЕ анулює попередні посилання, тож надруковані QR-коди не «згорять».
    request=True — вступ «за заявкою» (бот сам пускає лише гостей туси); None — так, якщо чат туси підключено.
    Режим: open | request | primary (основне посилання каналу, якщо створити своє не вийшло)."""
    if request is None:
        request = bool(db["party_chat_id"])
    for attempt in range(INVITE_ATTEMPTS):
        try:
            link = await bot.create_chat_invite_link(chat_id, name="Halloween бот", creates_join_request=request)
            return link.invite_link, ("request" if request else "open")
        except Exception as ex:
            log.warning("create_chat_invite_link failed (спроба %s): %s", attempt + 1, ex)
            # Telegram шле «тебе зробили адміном» на мить раніше, ніж реально додає бота в канал:
            # перша спроба дає «bot is not a member». Чекаємо й пробуємо ще.
            if "not a member" not in str(ex).lower() or attempt == INVITE_ATTEMPTS - 1:
                break
            await asyncio.sleep(INVITE_RETRY_DELAY)
    try:
        return (await bot.get_chat(chat_id)).invite_link, "primary"
    except Exception:
        return None, None


async def on_bot_status(bot: Bot, chat, actor, new_member) -> None:
    """Бота зробили адміном каналу / змінили права / прибрали."""
    status = new_member.status
    if status == "administrator":
        if not is_admin(actor.id):
            # найчастіше це власник, який додав бота в канал ДО /owner: запам'ятовуємо,
            # і /owner від цієї ж людини підключить канал без «прибери й додай знову»
            db["pending_channel"] = {"id": chat.id, "title": chat.title or "", "by": actor.id}
            store.save()
            log.warning("Бота додав у канал «%s» не-адмін %s: чекаю, поки він стане адміном", chat.title, actor.id)
            return
        await connect_channel(bot, chat.id, chat.title or "", new_member)
    elif chat.id == db["channel_id"] and status in ("left", "kicked", "member"):
        db["channel_id"] = None
        db["channel_link"] = None
        store.save()
        for a in admin_ids():
            await safe_send(bot, a, f"⚠️ Бот більше не адмін каналу «{esc(chat.title or '')}». Публікація на паузі.")


async def connect_channel(bot: Bot, chat_id: int, chat_title: str, new_member) -> None:
    title = esc(chat_title)
    db["pending_channel"] = None
    missing = missing_rights(new_member)
    warn = f"\n⚠️ Боту бракує прав: {', '.join(missing)}. Додай їх у налаштуваннях каналу." if missing else ""
    if db["channel_id"] == chat_id and db["channel_link"]:
        # той самий канал, змінились лише права: посилання НЕ чіпаємо (QR на постерах живі)
        store.save()
        if db["owner"]:
            await safe_send(bot, db["owner"], f"ℹ️ Права бота в каналі «{title}» змінено.{warn}",
                            disable_notification=not missing)
        return
    if db["channel_id"] != chat_id:
        db["members"].clear()                 # новий канал — старий список підписників не дійсний
    db["channel_id"] = chat_id
    db["channel_link"], db["channel_link_mode"] = await make_invite_link(bot, chat_id)
    store.save()
    log.info("Канал підключено: %s (%s)", chat_title, chat_id)
    if not db["channel_link"]:
        warn += "\n⚠️ Не вдалося створити посилання-запрошення: дай боту право «Запрошувати користувачів»."
    for a in admin_ids():
        await safe_send(bot, a, f"✅ Канал «{title}» підключено! Костюми публікуватимуться туди.{warn}")


async def connect_pending(bot: Bot, user_id: int) -> bool:
    """Бота додали в канал, коли ця людина ще не була адміном (наприклад, до /owner).
    Тепер вона адмін — перевіряємо, що бот і досі адмін того каналу, і підключаємо."""
    p = db.get("pending_channel")
    if not p or p.get("by") != user_id or not is_admin(user_id) or db["channel_id"]:
        return False
    try:
        me = await bot.get_chat_member(p["id"], bot.id)
    except Exception as ex:
        log.warning("Відкладений канал %s недоступний: %s", p["id"], ex)
        db["pending_channel"] = None
        store.save()
        return False
    if me.status != "administrator":
        db["pending_channel"] = None
        store.save()
        return False
    await connect_channel(bot, p["id"], p.get("title", ""), me)
    return True
