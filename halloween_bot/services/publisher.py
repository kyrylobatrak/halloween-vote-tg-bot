"""Публікація заявок у канал по черзі (ліміт Telegram ~20 постів/хв)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InputMediaPhoto

from ..config import settings
from ..domain.entries import partner_ids
from ..domain.people import admin_ids
from ..runtime import rt
from ..storage import db, store
from ..ui.keyboards import pair_confirm_kb, vote_kb
from ..ui.texts import caption, confirm_link, esc, post_link
from .messaging import channel_slot, safe_send
from .notify import notify_admins_photo

log = logging.getLogger("halloween.publisher")

POST_ATTEMPTS = 5
RETRY_DELAY = 5.0          # с між спробами, якщо мережа на хотспоті моргнула


def queue_position(num: int) -> tuple[int, int]:
    """(місце в черзі, орієнтовно секунд до публікації)."""
    ahead = sum(1 for e in db["entries"].values()
                if e["status"] == "queued" and e["num"] < num and e["num"] != rt.posting_now)
    pos = ahead + 1 + (1 if rt.posting_now and rt.posting_now != num else 0)
    per_min = max(1, settings.channel_per_min)
    eta = max(pos * settings.post_interval, pos * 60 / per_min)
    return pos, int(eta)


async def enqueue(num: int) -> None:
    await rt.post_queue.put(num)


def requeue_pending() -> int:
    """Після перезапуску допостити те, що не встигло вийти."""
    nums = sorted(int(k) for k, e in db["entries"].items() if e["status"] == "queued")
    for n in nums:
        rt.post_queue.put_nowait(n)
    return len(nums)


async def after_publish(bot: Bot, e: dict) -> None:
    num = e["num"]
    if e.pop("restored", False):
        store.save()
        await safe_send(bot, e["owner"], f"♻️ Твій костюм <b>№{num}</b> повернули в конкурс "
                                         f"разом з голосами.\n{post_link(e)}")
        return
    text = f"✅ Твій костюм опубліковано! Номер <b>№{num}</b>\n{post_link(e)}"
    if e["nom"] == "pair" and not e.get("partner_id"):         # адмін опублікував без підтвердження
        text += partner_hint(e)
    await safe_send(bot, e["owner"], text)
    if e.get("partner_id"):
        await safe_send(bot, e["partner_id"], f"✅ Ваш парний костюм опубліковано! <b>№{num}</b>\n{post_link(e)}")
    await notify_admins_photo(bot, e)


def partner_hint(e: dict) -> str:
    text = f"\n\n👥 Перешли партнеру це посилання, щоб він підтвердив участь:\n{confirm_link(e['num'])}"
    if e.get("partner"):
        text += f"\n(Якщо @{esc(e['partner'])} уже запускав бота, я надіслав йому запит сам.)"
    else:
        text += "\nКоли він його відкриє, тобі прийде запит: натисни «✅ Так, це мій партнер»."
    return text


async def ask_partner(bot: Bot, e: dict) -> None:
    """Партнер, якого бот знає (запускав бота), отримує кнопки «Так, це я» / «Це не я»."""
    pid = next(iter(partner_ids(e)), None)
    if pid and pid != e["owner"] and not e.get("partner_id"):
        await safe_send(bot, pid, f"👥 {esc(e['owner_tag'])} додав(-ла) тебе в парний костюм "
                                  f"<b>№{e['num']}</b> «{esc(e['title'])}».\nЦе ти?\n\n"
                                  "Пост з'явиться в каналі, щойно ти підтвердиш.",
                        reply_markup=pair_confirm_kb(e["num"]))


async def accept_entry(bot: Bot, e: dict) -> str:
    """Заявку щойно подали. Соло — у чергу на публікацію; пара — чекає, поки партнер підтвердить.
    Повертає текст для автора."""
    num = e["num"]
    if e["nom"] == "pair" and not e.get("partner_id"):
        e["status"] = "waiting"
        store.save()
        await ask_partner(bot, e)
        who = f"@{esc(e['partner'])}" if e.get("partner") else "партнер"
        return (f"🎃 Прийнято! Номер вашої пари <b>№{num}</b>.\n"
                f"⏳ <b>У канал пара потрапить, щойно {who} підтвердить участь.</b>" + partner_hint(e))
    await enqueue(num)
    pos, eta = queue_position(num)
    if pos <= 2:
        when = "Публікую в канал, це кілька секунд…"
    else:
        when = (f"Зараз багато заявок: ти <b>{pos}-й у черзі</b>, пост з'явиться приблизно за "
                f"<b>{eta // 60} хв {eta % 60} с</b> (Telegram дозволяє ~20 постів на хвилину). "
                "Я напишу, щойно опублікую. Подавати вдруге не треба 🙂")
    return f"🎃 Прийнято! Твій номер <b>№{num}</b>.\n{when}"


async def publish_one(bot: Bot, num: int) -> None:
    e = db["entries"].get(str(num))
    if not e or e["status"] != "queued":
        return
    while not db["channel_id"]:                  # канал ще не підключили — чекаємо
        await asyncio.sleep(5)
    rt.posting_now = num
    msg = None
    for _ in range(POST_ATTEMPTS):
        try:
            await channel_slot()
            msg = await bot.send_photo(db["channel_id"], e["photo"], caption=caption(e),
                                       reply_markup=vote_kb(num))
            break
        except TelegramRetryAfter as ex:
            await asyncio.sleep(ex.retry_after + 1)
        except Exception as ex:                  # мережа на хотспоті може моргнути
            log.warning("post №%s failed: %s", num, ex)
            await asyncio.sleep(RETRY_DELAY)
    rt.posting_now = None
    if e["status"] != "queued":                  # поки ретраїли, заявку зняли
        if msg:
            try:
                await bot.delete_message(msg.chat.id, msg.message_id)
            except TelegramBadRequest:
                pass
        return
    if msg is None:
        e["status"] = "failed"
        store.save()
        log.error("№%s не вдалося опублікувати після %s спроб", num, POST_ATTEMPTS)
        for a in admin_ids():
            await safe_send(bot, a, f"⚠️ Не вдалося опублікувати №{num}. Спробуй /repost {num}")
        return
    e["status"] = "published"
    e["msg_id"] = msg.message_id
    e["chat_id"] = msg.chat.id
    store.save()
    log.info("Опубліковано №%s", num)
    await after_publish(bot, e)


async def publisher_loop(bot: Bot) -> None:
    while True:
        num = await rt.post_queue.get()
        try:
            await publish_one(bot, num)
        except Exception:                        # одна зламана заявка не зупиняє чергу
            log.exception("publisher: №%s", num)


async def refresh_post(bot: Bot, e: dict, new_photo: bool = False) -> None:
    """Оновити пост у каналі (підпис або фото)."""
    if e["status"] != "published" or not e.get("msg_id"):
        return
    chat = e.get("chat_id") or db["channel_id"]
    try:
        if new_photo:
            await bot.edit_message_media(
                chat_id=chat, message_id=e["msg_id"], reply_markup=vote_kb(e["num"]),
                media=InputMediaPhoto(media=e["photo"], caption=caption(e), parse_mode=ParseMode.HTML),
            )
        else:
            await bot.edit_message_caption(chat_id=chat, message_id=e["msg_id"],
                                           caption=caption(e), reply_markup=vote_kb(e["num"]))
    except TelegramBadRequest as ex:
        if "not modified" not in str(ex):
            log.warning("edit №%s failed: %s", e["num"], ex)
