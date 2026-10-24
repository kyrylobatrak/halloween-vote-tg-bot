"""Надійна відправка повідомлень з урахуванням лімітів Telegram."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from ..config import settings
from ..runtime import rt

log = logging.getLogger("halloween.messaging")


async def safe_send(bot: Bot, uid: int, text: str, **kw) -> bool:
    """Надіслати й не впасти: заблокував бота, обрив мережі тощо → False."""
    try:
        await bot.send_message(uid, text, **kw)
        return True
    except TelegramRetryAfter as ex:
        await asyncio.sleep(ex.retry_after + 1)
        return await safe_send(bot, uid, text, **kw)
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as ex:                       # мережа на хотспоті: не валимо розсилку
        log.warning("send to %s failed: %s", uid, ex)
        return False


async def mass_send(bot: Bot, jobs: list[tuple[int, str, dict]]) -> tuple[int, int]:
    """Розсилка паралельно, але не швидше за settings.send_rate повідомлень/с (ліміт Telegram ~30/с).
    Повертає (доставлено, не доставлено)."""
    sem = asyncio.Semaphore(50)
    results: list[bool] = []

    async def one(uid: int, text: str, kw: dict) -> None:
        async with sem:
            results.append(await safe_send(bot, uid, text, **kw))

    tasks = []
    for uid, text, kw in jobs:
        tasks.append(asyncio.create_task(one(uid, text, kw)))
        await asyncio.sleep(1 / settings.send_rate)
    await asyncio.gather(*tasks)
    ok = sum(results)
    return ok, len(results) - ok


async def channel_slot() -> None:
    """Спільний ліміт для всього, що бот пише в канал (пости й оголошення):
    не більше channel_per_min за будь-які 60 с і не частіше, ніж раз на post_interval."""
    sends = rt.channel_sends
    while True:
        now = time.time()
        while sends and now - sends[0] >= 60:
            sends.popleft()
        wait = 0.0
        if len(sends) >= settings.channel_per_min:
            wait = 60 - (now - sends[0]) + 0.1
        elif sends and now - sends[-1] < settings.post_interval:
            wait = settings.post_interval - (now - sends[-1])
        if wait <= 0:
            sends.append(now)
            return
        await asyncio.sleep(wait)


async def set_buttons(message, markup=None) -> None:
    """Змінити/прибрати кнопки під повідомленням. Подвійний тап («message is not modified»)
    або надто старе повідомлення — не привід падати."""
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass


TEXT_LIMIT = 4000        # ліміт Telegram — 4096 символів, лишаємо запас


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Поділити довгий текст на шматки ≤ limit по межах рядків (HTML-теги в нас не переходять
    через рядок, тож розмітка не ламається). Рядок, довший за limit, ріжеться примусово."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur or not parts:
        parts.append(cur)
    return parts


async def answer_long(message, text: str, **kw) -> None:
    """message.answer для текстів будь-якої довжини: кнопки (reply_markup) — під останнім шматком."""
    parts = split_text(text)
    markup = kw.pop("reply_markup", None)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await message.answer(part, reply_markup=markup if last else None, **kw)
