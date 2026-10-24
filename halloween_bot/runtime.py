"""Стан процесу, який не зберігається на диск: черга постів, кеші, час старту."""
from __future__ import annotations

import asyncio
import time
from collections import deque


class Runtime:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bot_username = ""
        self.started = time.time()
        self.awake = False                              # захист від сну Windows увімкнено
        self.post_queue: asyncio.Queue[int] = asyncio.Queue()
        self.posting_now: int | None = None             # номер, який публікується просто зараз
        self.channel_sends: deque[float] = deque()      # повідомлення в канал за останню хвилину
        self.member_checked: dict[int, float] = {}      # коли востаннє перевіряли підписку
        self.party_cache: dict[int, tuple[bool, float]] = {}  # чи в чаті туси: (так/ні, коли перевіряли)
        self.last_tap: dict[tuple[int, int], float] = {}  # (user, костюм) → час останнього тапу
        self.album_seen: dict[str, float] = {}          # media_group_id → коли побачили
        self.save_alerted = False                       # власнику вже писали про збій запису
        self.last_local_backup = 0.0
        self.last_tg_backup = 0.0
        self.errors: deque[str] = deque(maxlen=20)      # неперехоплені помилки обробників (/health)


rt = Runtime()
