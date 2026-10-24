"""Голос кнопкою під постом. Повертає текст спливаючого вікна."""
from __future__ import annotations

import time

from aiogram import Bot

from ..config import NOMS, settings
from ..domain.entries import get, is_participant
from ..domain.people import note_user
from ..runtime import rt
from ..storage import db, store
from .membership import is_channel_member
from .party import party_gate


async def cast_vote(bot: Bot, user, num: int) -> str:
    if not db["vote_open"]:
        return "⛔ Голосування зараз закрите"
    if user.id in db["banned"]:
        return "🚫 Ти не можеш голосувати"
    e = get(num)
    if not e or e["status"] != "published":
        return "Цей костюм ще не опубліковано" if e and e["status"] != "removed" else "Цей костюм знято з конкурсу"
    if is_participant(e, user):
        return "😏 За свій костюм голосувати не можна"
    if why := await party_gate(bot, user):
        return why
    member = await is_channel_member(bot, user.id)
    if member is None:
        return "⚠️ Не вдалося перевірити підписку. Тапни ще раз."
    if not member:
        return "🔒 Голосувати можуть лише підписники каналу.\nПідпишись і тапни ще раз."

    # поки чекали Telegram, костюм могли зняти або голосування закрити.
    # Далі — жодного await до кінця зміни голосу, тож паралельні тапи не перемішаються.
    e = get(num)
    if not db["vote_open"]:
        return "⛔ Голосування щойно закрилось"
    if not e or e["status"] != "published":
        return "Цей костюм щойно знято з конкурсу"

    note_user(user)
    nom = e["nom"]
    short = NOMS[nom]["short"]
    mine = db["votes"].setdefault(str(user.id), {}).setdefault(nom, [])

    # повільний інтернет: людина не дочекалась відповіді й тапнула ще раз — не знімаємо голос
    key, now = (user.id, num), time.time()
    if now - rt.last_tap.get(key, 0) < settings.tap_guard:
        state = "✅ Голос за №{} уже зараховано" if num in mine else "↩️ Голос за №{} уже знято"
        return state.format(num) + "\n(повторний тап проігноровано)"
    if len(rt.last_tap) > 20000:
        for k, t in list(rt.last_tap.items()):
            if now - t > settings.tap_guard:
                del rt.last_tap[k]

    if num in mine:
        mine.remove(num)
        rt.last_tap[key] = now
        store.log_vote(user.id, nom, num, -1)
        store.save()
        return f"↩️ Голос за №{num} знято.\nУ номінації «{short}»: {len(mine)}/{settings.max_votes}"
    if len(mine) >= settings.max_votes:
        lst = ", ".join(f"№{n}" for n in mine)
        return (f"Ліміт {settings.max_votes} голосів у «{short}» вичерпано.\nТвої голоси: {lst}\n"
                "Щоб змінити, тапни ще раз під одним із них.")[:200]
    mine.append(num)
    rt.last_tap[key] = now
    store.log_vote(user.id, nom, num, 1)
    store.save()
    return f"✅ Голос за №{num} зараховано!\nУ номінації «{short}»: {len(mine)}/{settings.max_votes}"
