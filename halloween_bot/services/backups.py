"""Фонові задачі збереження: запис на диск, локальні бекапи, бекапи власнику в Telegram."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import BufferedInputFile

from ..config import settings
from ..runtime import rt
from ..storage import db, store
from ..ui.texts import esc
from .messaging import safe_send

log = logging.getLogger("halloween.backups")


async def flush_and_alert(bot: Bot | None) -> bool:
    """Записати зміни; при збої — один раз попередити власника, при відновленні — сказати, що ок."""
    ok = await store.flush()
    owner = db["owner"]
    if ok and rt.save_alerted:
        rt.save_alerted = False
        if bot and owner:
            await safe_send(bot, owner, "✅ Запис data.json знову працює.")
    elif not ok and not rt.save_alerted:
        rt.save_alerted = True
        if bot and owner:
            await safe_send(bot, owner,
                            f"🆘 Не можу записати data.json: {esc(store.last_error or '')}\n"
                            "Голоси поки в пам'яті. Закрий програми, що можуть тримати файл "
                            "(Excel, OneDrive, антивірус), і зроби /backup.")
    return ok


async def saver_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(settings.save_every)
        await flush_and_alert(bot)


async def send_backup(bot: Bot, chat_id: int, silent: bool = False) -> None:
    name = f"backup_{time.strftime('%H-%M')}.zip"
    await bot.send_document(chat_id, BufferedInputFile(store.backup_zip(), name),
                            caption="💾 Бекап: розпакуй data.json і votes.log у папку бота",
                            disable_notification=silent)


async def backup_tick(bot: Bot, state: dict) -> None:
    """Один крок backup_loop (окремо, щоб легко тестувати)."""
    now = time.time()
    if now - state["last_local"] >= settings.local_backup_every and store.version != state["local_version"]:
        try:
            await asyncio.to_thread(store.local_backup)
            state["local_version"], state["last_local"] = store.version, now
            rt.last_local_backup = now
        except OSError as ex:
            log.warning("local backup failed: %s", ex)
    every = settings.vote_backup_every if db["vote_open"] else settings.backup_every
    if db["owner"] and now - state["last_tg"] >= every and store.version != state["tg_version"]:
        try:
            await send_backup(bot, db["owner"], silent=True)
            state["tg_version"], state["last_tg"] = store.version, now
            rt.last_tg_backup = now
        except Exception as ex:
            log.warning("backup failed: %s", ex)


async def backup_loop(bot: Bot) -> None:
    """Копія в backups/ щохвилини + бекап власнику в Telegram
    (кожні 3 хв під час голосування, інакше кожні 15 хв). Лише якщо щось змінилось."""
    state = {"local_version": -1, "tg_version": -1, "last_local": time.time(), "last_tg": time.time()}
    while True:
        await asyncio.sleep(20)
        await backup_tick(bot, state)
