"""Сповіщення адмінам і журнал їхніх дій."""
from __future__ import annotations

import logging
import time

from aiogram import Bot

from ..domain.people import admin_ids, note_user, tag_of
from ..storage import db, store
from ..ui.keyboards import admin_mod_kb
from ..ui.texts import caption, esc, similar_note
from .messaging import safe_send

log = logging.getLogger("halloween.admin")


def wants(admin_id: int, important: bool) -> bool:
    """/quiet-адмінам шлемо тільки важливе (схожі фото, відмови партнерів, збої)."""
    return important or admin_id not in db["quiet_admins"]


async def notify_admins_photo(bot: Bot, e: dict, header: str = "") -> None:
    """Копія заявки адмінам з кнопками 🗑 / 🚫."""
    important = bool(e.get("similar_to"))
    for a in admin_ids():
        if not wants(a, important):
            continue
        try:
            await bot.send_photo(a, e["photo"], caption=header + caption(e) + similar_note(e),
                                 reply_markup=admin_mod_kb(e["num"]), disable_notification=True)
        except Exception:
            pass


async def notify_admins_text(bot: Bot, text: str, important: bool = False, **kw) -> None:
    for a in admin_ids():
        if wants(a, important):
            await safe_send(bot, a, text, **kw)


async def admin_action(bot: Bot, actor, action: str, detail: str = "",
                       alert: bool = False, notify: bool = True) -> None:
    """Журнал дій адмінів (/adminlog). Власнику — копія кожної дії, яку зробив не він."""
    if actor:
        note_user(actor)
    log.info("Адмін %s: %s %s", tag_of(actor) if actor else "бот", action, detail)
    db["admin_log"].append([int(time.time()), actor.id if actor else 0, action, detail])
    store.save()
    if notify and db["owner"] and (actor is None or actor.id != db["owner"]):
        who = esc(tag_of(actor)) if actor else "🤖 бот"
        await safe_send(bot, db["owner"], f"📝 {who}: {esc(action)} {esc(detail)}".strip(),
                        disable_notification=not alert)
