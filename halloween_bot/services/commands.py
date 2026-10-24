"""Меню команд («/» біля поля вводу): гостям — свої, адмінам і власнику — розширені."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from ..domain.people import admin_ids
from ..storage import db

log = logging.getLogger("halloween.commands")

GUEST = [
    ("start", "🎃 Головне меню"),
    ("my", "🧛 Мої костюми"),
    ("myvotes", "🗳 Мої голоси"),
    ("cancel", "✖️ Скасувати подачу"),
]
ADMIN = GUEST + [
    ("admin", "🛠 Усі адмін-команди"),
    ("stats", "📊 Статистика"),
    ("results", "🏆 Результати (топ-N)"),
    ("health", "🩺 Стан бота"),
    ("open_submit", "🟢 Відкрити прийом костюмів"),
    ("close_submit", "🔴 Закрити прийом костюмів"),
    ("open_vote", "🟢 Відкрити голосування"),
    ("close_vote", "🔴 Закрити голосування"),
    ("remind", "🔔 Нагадати про голоси"),
    ("audit", "🕵️ Перевірка накруток"),
]
OWNER = ADMIN + [
    ("admins", "👥 Список адмінів"),
    ("backup", "💾 Бекап зараз"),
]


def _cmds(rows) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in rows]


async def sync_commands(bot: Bot, removed: int | None = None) -> None:
    """Не критично: якщо Telegram не прийняв меню, бот працює й без нього."""
    try:
        await bot.set_my_commands(_cmds(GUEST), scope=BotCommandScopeDefault())
        for uid in admin_ids():
            rows = OWNER if uid == db["owner"] else ADMIN
            await bot.set_my_commands(_cmds(rows), scope=BotCommandScopeChat(chat_id=uid))
        if removed is not None:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=removed))
    except Exception as ex:
        log.warning("Не вдалося оновити меню команд: %s", ex)
