"""Партнер у парному костюмі.

Два шляхи:
  • автор вказав @username — партнер підтверджує сам (кнопкою або посиланням):
    Telegram гарантує, що це саме та людина;
  • у партнера нема username — він відкриває посилання, а **автор пари схвалює** запит.
    Так посилання, що «втекло» в загальний чат, не дасть чужому записатись у пару.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from ..domain.entries import active, get, pair_of, person
from ..domain.people import name_of, note_user, tag_of, username_of
from ..storage import db, store
from ..ui.keyboards import admin_mod_kb, partner_approve_kb
from ..ui.texts import esc
from .messaging import safe_send
from .notify import notify_admins_text
from .party import party_gate
from .publisher import enqueue, refresh_post

log = logging.getLogger("halloween.partners")


def _block(e: dict | None, user, num: int) -> str | None:
    """Чому `user` не може стати партнером у парі №num (None — може)."""
    if not e or e["nom"] != "pair" or not active(e):
        return "Цієї пари вже нема в конкурсі."
    if e["owner"] == user.id:
        return "Це твоя ж заявка 🙂 Посилання треба переслати партнеру."
    if e.get("partner_id"):
        if e["partner_id"] == user.id:
            return f"✅ Ти вже підтверджений партнер у парі №{num}."
        return "У цієї пари вже є підтверджений партнер."
    if user.id in db["banned"]:
        return "🚫 Ти не можеш брати участь."
    other = pair_of(user, exclude=e)
    if other:
        return f"Ти вже є в парі №{other['num']}. Одна людина = одна пара."
    return None


async def _bind(bot: Bot, e: dict, uid: int, display: str) -> str:
    """Записати підтвердженого партнера. Повертає приписку до повідомлень (про публікацію)."""
    e["partner_id"] = uid
    if not e.get("partner"):
        e["partner_name"] = display
    e.pop("partner_requests", None)
    my = db["votes"].get(str(uid), {}).get("pair", [])
    if e["num"] in my:                               # голос за свою ж пару анулюємо
        my.remove(e["num"])
    note = ""
    if e["status"] == "waiting":                     # пара чекала на підтвердження — тепер у канал
        e["status"] = "queued"
        note = "\n📮 Публікую пару в канал, це кілька секунд."
    store.save()
    if note:
        await enqueue(e["num"])
    await refresh_post(bot, e)
    log.info("Пара №%s: партнер %s (%s) підтверджений", e["num"], display, uid)
    return note


async def confirm_partner(bot: Bot, user, num: int) -> str:
    """Партнер натиснув «Так, це я» або відкрив посилання-підтвердження."""
    e = get(num)
    if reason := _block(e, user, num) or await party_gate(bot, user):
        return reason
    note_user(user)
    if e.get("partner"):                             # автор вказав @username — перевіряємо його
        if not user.username or user.username.lower() != e["partner"].lower():
            return f"Це посилання для @{esc(e['partner'])}, а не для тебе."
        note = await _bind(bot, e, user.id, tag_of(user))
        await safe_send(bot, e["owner"], f"🤝 {esc(tag_of(user))} підтвердив(-ла) участь у вашій парі №{num}!{note}")
        return f"✅ Готово! Ти в парі <b>№{num}</b> «{esc(e['title'])}» разом з {esc(e['owner_tag'])}.{note}"

    # у партнера нема username — питаємо автора
    requests = e.setdefault("partner_requests", [])
    if user.id in requests:
        return "Запит уже надіслано автору пари, чекай на підтвердження 🙂"
    requests.append(user.id)
    store.save()
    log.info("Пара №%s: запит від %s (%s) чекає схвалення автора", num, tag_of(user), user.id)
    await safe_send(bot, e["owner"],
                    f"👥 <b>{esc(tag_of(user))}</b> хоче приєднатися як партнер до вашої пари "
                    f"<b>№{num}</b> «{esc(e['title'])}».\nЦе справді твій партнер?",
                    reply_markup=partner_approve_kb(num, user.id))
    return ("📨 Запит надіслано автору пари. Щойно він підтвердить, я напишу тобі, "
            "і твоє ім'я з'явиться в пості 🙂")


async def answer_request(bot: Bot, owner, num: int, uid: int, approve: bool) -> str:
    """Автор пари схвалив або відхилив запит партнера без username. Повертає текст для автора."""
    e = get(num)
    if not e or e["owner"] != owner.id:
        return "Це не твоя пара."
    if uid not in e.get("partner_requests", []):
        return "Цей запит уже неактуальний."
    if not approve:
        e["partner_requests"].remove(uid)
        store.save()
        await safe_send(bot, uid, f"❌ Автор пари №{num} не підтвердив тебе як партнера. "
                                  "Якщо це помилка, напиши йому напряму.")
        return f"Ок, запит від {esc(name_of(uid))} відхилено."
    if reason := _block(e, person(uid, username_of(uid)), num):
        e["partner_requests"].remove(uid)
        store.save()
        return f"Не можу додати: {esc(reason)}"
    note = await _bind(bot, e, uid, name_of(uid))
    await safe_send(bot, uid, f"✅ Автор підтвердив! Ти в парі <b>№{num}</b> «{esc(e['title'])}» "
                              f"разом з {esc(e['owner_tag'])}.{note}")
    return f"🤝 {esc(name_of(uid))} тепер твій партнер у парі №{num}.{note}"


async def deny_partner(bot: Bot, user, num: int) -> None:
    e = get(num)
    if not e or not active(e):
        return
    who = esc(tag_of(user))
    await safe_send(bot, e["owner"], f"⚠️ {who} каже, що не є твоїм партнером у парі №{num}. "
                                      "Якщо це помилка, зніми заявку в «📋 Мої костюми» і подай заново.")
    await notify_admins_text(bot, f"⚠️ {who} відмовився бути партнером у парі №{num}.",
                             important=True, reply_markup=admin_mod_kb(num))
