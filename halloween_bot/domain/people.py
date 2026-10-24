"""Люди й ролі: власник, адміни, відомі користувачі."""
from __future__ import annotations

from ..storage import db, store


def is_owner(uid: int) -> bool:
    return uid == db["owner"]


def is_admin(uid: int) -> bool:
    return uid == db["owner"] or uid in db["admins"]


def admin_ids() -> list[int]:
    ids = [db["owner"]] if db["owner"] else []
    return ids + [a for a in db["admins"] if a != db["owner"]]


def tag_of(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


def note_user(user) -> bool:
    """Запам'ятати username та ім'я (без позначки на збереження). True, якщо щось змінилось."""
    changed = False
    if user.username and db["usernames"].get(user.username.lower()) != user.id:
        db["usernames"][user.username.lower()] = user.id
        changed = True
    if db["names"].get(str(user.id)) != tag_of(user):
        db["names"][str(user.id)] = tag_of(user)
        changed = True
    return changed


def remember(user) -> None:
    """Людина відкрила бота: тепер їй можна писати (/broadcast, /remind)."""
    changed = note_user(user)
    if user.id not in db["users"]:
        db["users"].append(user.id)
        changed = True
    if changed:
        store.save()


def name_of(uid: int) -> str:
    return db["names"].get(str(uid)) or str(uid)


def username_of(uid: int) -> str | None:
    for name, i in db["usernames"].items():
        if i == uid:
            return name
    return None


def resolve_user(arg: str | None) -> int | None:
    """`@username` або числовий id → id (лише для тих, хто вже писав боту)."""
    arg = (arg or "").strip().lstrip("@").lower()
    if arg.isdigit():
        return int(arg)
    return db["usernames"].get(arg)
