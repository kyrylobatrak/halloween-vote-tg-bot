"""Заявки (костюми): хто учасник, хто в якій парі, хто що може подати."""
from __future__ import annotations

import time
from types import SimpleNamespace

from ..storage import db, store


def get(num) -> dict | None:
    return db["entries"].get(str(num))


def active(e: dict) -> bool:
    return e["status"] != "removed"


def partner_ids(e: dict) -> set[int]:
    ids = set()
    if e.get("partner_id"):
        ids.add(e["partner_id"])
    p = e.get("partner")
    if p and db["usernames"].get(p.lower()):
        ids.add(db["usernames"][p.lower()])
    return ids


def is_participant(e: dict, user) -> bool:
    """Автор або партнер (за id, за підтвердженням або за @username)."""
    if e["owner"] == user.id or user.id in partner_ids(e):
        return True
    p = e.get("partner")
    return bool(p and user.username and p.lower() == user.username.lower())


def pair_of(user, exclude: dict | None = None) -> dict | None:
    """Активна пара, де людина — автор або ПІДТВЕРДЖЕНИЙ партнер.

    Того, кого лише вписали (@username) і хто ще не підтвердив, не рахуємо: інакше будь-хто,
    навіть помилившись у username, заблокував би людині власну пару. Що людина в одній парі,
    перевіряється ще раз, коли вона підтверджує участь."""
    for e in db["entries"].values():
        if (e is not exclude and e["nom"] == "pair" and active(e)
                and user.id in (e["owner"], e.get("partner_id"))):
            return e
    return None


def person(uid: int | None, username: str | None) -> SimpleNamespace:
    """Мінімальний «користувач» для перевірок, коли маємо лише id та/або username."""
    return SimpleNamespace(id=uid or -1, username=username)


def entry_block(user, nom: str) -> str | None:
    """Чому людина не може подати ще одну заявку в цій номінації."""
    if nom == "solo":
        for e in db["entries"].values():
            if e["owner"] == user.id and e["nom"] == "solo" and active(e):
                return f"У тебе вже є костюм у соло: №{e['num']} 🙂"
        return None
    e = pair_of(user)
    if e:
        role = "автор" if e["owner"] == user.id else "партнер"
        return f"Ти вже є в парі №{e['num']} ({role}). Одна людина = одна пара."
    return None


def submit_block_reason(user, nom: str | None = None) -> str | None:
    if user.id in db["banned"]:
        return "🚫 Ти не можеш подавати заявки."
    if not db["submit_open"]:
        return "⏳ Прийом заявок зараз закритий."
    if nom:
        return entry_block(user, nom)
    return None


def partner_block(user, partner: str) -> str | None:
    """Чи можна вказати @partner партнером."""
    if user.username and partner.lower() == user.username.lower():
        return "Партнер має бути іншою людиною 😄"
    pid = db["usernames"].get(partner.lower())
    if pid == user.id:
        return "Партнер має бути іншою людиною 😄"
    if pid in db["banned"]:
        return "Ця людина не може брати участь у конкурсі."
    other = pair_of(person(pid, partner))
    if other:
        return f"@{partner} вже є в парі №{other['num']}. Одна людина = одна пара."
    return None


def create(user, owner_tag: str, d: dict) -> dict:
    """Нова заявка зі статусом queued (публікує її publisher)."""
    db["seq"] += 1
    num = db["seq"]
    e = {
        "num": num,
        "owner": user.id,
        "owner_tag": owner_tag,
        "nom": d["nom"],
        "partner": d.get("partner"),
        "partner_id": None,
        "partner_name": None,
        "title": d["title"],
        "photo": d["photo"],
        "photo_uid": d["photo_uid"],
        "phash": d.get("phash"),
        "similar_to": d.get("similar_to"),
        "replaced": 0,
        "status": "queued",       # queued → published | failed | removed
        "msg_id": None,
        "created": int(time.time()),
    }
    db["entries"][str(num)] = e
    store.save()
    return e


def own_entry(num, uid: int) -> dict | None:
    e = get(num)
    return e if e and active(e) and e["owner"] == uid else None


def restore_conflict(e: dict) -> dict | None:
    """Інший активний костюм автора/партнера, через який не можна повернути `e`."""
    from .people import username_of
    if e["nom"] == "solo":
        return next((x for x in db["entries"].values() if x is not e and x["owner"] == e["owner"]
                     and x["nom"] == "solo" and active(x)), None)
    conflict = pair_of(person(e["owner"], username_of(e["owner"])), exclude=e)
    for pid in partner_ids(e):
        conflict = conflict or pair_of(person(pid, username_of(pid)), exclude=e)
    return conflict
