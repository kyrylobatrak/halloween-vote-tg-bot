"""Видалення, відновлення й перевірка постів."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from ..config import settings
from ..domain.entries import get, restore_conflict
from ..domain.people import is_admin, name_of
from ..storage import db, store
from ..ui.keyboards import vote_kb
from ..ui.texts import entry_label
from .notify import admin_action
from .publisher import enqueue

log = logging.getLogger("halloween.moderation")


async def remove_entry(bot: Bot, num: int, ban: bool = False, by: str = "admin") -> bool:
    """Зняти костюм з конкурсу. by: admin | self | system.
    Голоси анулюються, але запам'ятовуються, щоб /restore міг їх повернути."""
    e = get(num)
    if not e or e["status"] == "removed":
        return False
    # усі зміни в даних — ДО першого await: інакше друге одночасне видалення (подвійний тап,
    # кнопка + /del) пройшло б перевірку вище й затерло список голосів для /restore
    e["status"] = "removed"
    e["removed_by"] = by
    e["removed_at"] = int(time.time())
    e["removed_ban"] = ban
    voters = []
    for uid, user_votes in db["votes"].items():
        lst = user_votes.get(e["nom"])
        if lst and num in lst:
            lst.remove(num)
            voters.append(int(uid))
            store.log_vote(int(uid), e["nom"], num, store.REMOVED)
    e["removed_votes"] = voters
    if ban:
        ban_user(e["owner"])
    store.save()
    if e.get("msg_id"):
        await hide_post(bot, e)
    return True


async def hide_post(bot: Bot, e: dict) -> None:
    """Прибрати пост знятого костюма. Telegram не дає боту видаляти пости, старші за 48 год, —
    тоді хоча б прибираємо кнопку й пишемо в підписі, що костюм знято."""
    chat, mid = e.get("chat_id") or db["channel_id"], e["msg_id"]
    try:
        await bot.delete_message(chat, mid)
        return
    except TelegramBadRequest as ex:
        if "not found" in str(ex).lower():
            return                                # уже видалили руками
        log.warning("Не вдалося видалити пост №%s (%s) — позначаю як знятий", e["num"], ex)
    except Exception as ex:                       # мережа моргнула
        log.warning("Не вдалося видалити пост №%s: %s", e["num"], ex)
    try:
        await bot.edit_message_caption(chat_id=chat, message_id=mid, reply_markup=None,
                                       caption=f"🚫 <b>№{e['num']} знято з конкурсу</b>")
    except Exception as ex:
        log.warning("І позначити пост №%s не вдалося (видали його в каналі вручну): %s", e["num"], ex)


def ban_refusal(num: int) -> str | None:
    """Адміна не банимо: забанений адмін лишився б із адмін-командами. Спершу /deladmin."""
    e = get(num)
    if e and is_admin(e["owner"]):
        return (f"№{num} подав адмін ({name_of(e['owner'])}). Забанити адміна не можна: "
                f"власник спершу прибирає його з адмінів (/deladmin), або просто зніми костюм: /del {num}")
    return None


def ban_user(uid: int) -> int:
    """Бан: людина не подає й не голосує, а всі її голоси знімаються (і зберігаються для /unban).
    Повертає, скільки голосів знято."""
    if uid not in db["banned"]:
        db["banned"].append(uid)
    taken = db["votes"].pop(str(uid), {})
    saved = db["banned_votes"].setdefault(str(uid), {})
    count = 0
    for nom, nums in taken.items():
        for n in nums:
            store.log_vote(uid, nom, n, store.REMOVED)
            saved.setdefault(nom, []).append(n)
            count += 1
    store.save()
    return count


def unban_user(uid: int) -> tuple[int, int]:
    """Розбан: повертаємо голоси за костюми, що досі в конкурсі. (повернуто, не вдалося)."""
    if uid in db["banned"]:
        db["banned"].remove(uid)
    saved = db["banned_votes"].pop(str(uid), {})
    back = skipped = 0
    for nom, nums in saved.items():
        mine = db["votes"].setdefault(str(uid), {}).setdefault(nom, [])
        for n in nums:
            e = get(n)
            if e and e["status"] in ("published", "queued") and n not in mine and len(mine) < settings.max_votes:
                mine.append(n)
                store.log_vote(uid, nom, n, store.RESTORED)
                back += 1
            elif e and e["status"] == "removed" and uid not in e.get("removed_votes", []):
                e.setdefault("removed_votes", []).append(uid)   # костюм зараз знято: повернеться з /restore
                skipped += 1
            else:
                skipped += 1
    store.save()
    return back, skipped


@dataclass
class RestoreResult:
    ok: bool
    text: str
    restored_votes: int = 0
    skipped_votes: int = 0


async def restore_entry(bot: Bot, actor, num_arg: str) -> RestoreResult:
    arg = (num_arg or "").strip().lstrip("№")
    e = get(arg) if arg.isdigit() else None
    if not e:
        return RestoreResult(False, "Використання: /restore 47")
    if e["status"] != "removed":
        return RestoreResult(False, f"№{arg} не видалений (статус: {e['status']}).")
    if e.get("removed_by") == "self":
        return RestoreResult(False, f"№{arg} автор зняв сам. Повернути може тільки він, подавши костюм заново.")
    conflict = restore_conflict(e)
    if conflict:
        return RestoreResult(False, f"Не можу: учасник уже має інший костюм №{conflict['num']}. "
                                    "Спершу видали його, якщо треба.")
    unbanned = None
    if e.get("removed_ban") and e["owner"] in db["banned"]:
        unbanned = unban_user(e["owner"])
    back = skipped = 0
    for uid in e.get("removed_votes", []):
        if uid in db["banned"]:              # виборця за цей час забанили: голос «заморожуємо», /unban поверне
            db["banned_votes"].setdefault(str(uid), {}).setdefault(e["nom"], []).append(e["num"])
            continue
        lst = db["votes"].setdefault(str(uid), {}).setdefault(e["nom"], [])
        if e["num"] not in lst and len(lst) < settings.max_votes:
            lst.append(e["num"])
            store.log_vote(uid, e["nom"], e["num"], store.RESTORED)
            back += 1
        else:
            skipped += 1
    for k in ("removed_by", "removed_at", "removed_ban", "removed_votes"):
        e.pop(k, None)
    e["msg_id"] = None
    waiting = e["nom"] == "pair" and not e.get("partner_id")
    if waiting:                               # пара ще не підтверджена: знову чекає партнера, у канал — після
        e["status"] = "waiting"
    else:
        e["status"] = "queued"
        e["restored"] = True
    store.save()
    if not waiting:
        await enqueue(e["num"])
    await admin_action(bot, actor, "відновив", entry_label(e) + f" · голосів повернуто {back}", alert=True)
    text = (f"♻️ №{e['num']} відновлено: пара знову чекає підтвердження партнера, потім з'явиться в каналі"
            if waiting else f"♻️ №{e['num']} повертається в канал. Голосів відновлено: {back}")
    if skipped:
        text += (f"\n⚠️ Не вдалося повернути {skipped}: ці люди вже використали всі "
                 f"{settings.max_votes} голосів за інші костюми.")
    if unbanned:
        text += f"\n🔓 Автора розбанено, його власних голосів повернуто: {unbanned[0]}"
    return RestoreResult(True, text, back, skipped)


async def post_exists(bot: Bot, e: dict) -> bool | None:
    """Чи живий пост у каналі. Трюк: «редагуємо» кнопку на ту саму; Telegram відповідає
    «not modified», якщо пост є, і «not found», якщо його видалили руками. None = невідомо."""
    if not e.get("msg_id"):
        return None
    for _ in range(3):
        try:
            await bot.edit_message_reply_markup(chat_id=e.get("chat_id") or db["channel_id"],
                                                message_id=e["msg_id"], reply_markup=vote_kb(e["num"]))
            return True
        except TelegramRetryAfter as ex:
            await asyncio.sleep(ex.retry_after + 1)
        except TelegramBadRequest as ex:
            t = str(ex).lower()
            if "not modified" in t:
                return True
            if "not found" in t or "message_id_invalid" in t:
                return False                      # пост справді видалено
            # інші відмови («can't be edited», брак прав тощо) — НЕ доказ видалення: краще «невідомо»,
            # ніж зняти з конкурсу живий костюм (а /results перевіряє саме лідерів)
            return None
        except Exception:
            return None
    return None


async def drop_if_deleted(bot: Bot, e: dict) -> bool:
    """True, якщо пост видалили з каналу руками: тоді знімаємо костюм і його голоси."""
    if await post_exists(bot, e) is False:
        e["msg_id"] = None
        await remove_entry(bot, e["num"], by="system")
        await admin_action(bot, None, "пост видалено з каналу вручну, костюм знято (можна /restore)",
                           entry_label(e))
        log.info("№%s видалено з каналу вручну, знято з конкурсу", e["num"])
        return True
    return False


async def verify_all(bot: Bot, pause: float = 0.1) -> list[int]:
    dropped = []
    for e in [e for e in db["entries"].values() if e["status"] == "published"]:
        if await drop_if_deleted(bot, e):
            dropped.append(e["num"])
        await asyncio.sleep(pause)
    return dropped
