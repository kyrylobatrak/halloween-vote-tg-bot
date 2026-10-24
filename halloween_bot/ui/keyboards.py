"""Інлайн-клавіатури. callback_data — короткі префікси, їх розбирають handlers."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup

from ..config import NOMS, settings
from ..storage import db


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def vote_kb(num: int) -> InlineKeyboardMarkup:
    return kb([Btn(text="🗳 Голосувати", callback_data=f"v:{num}")])


def channel_kb() -> InlineKeyboardMarkup | None:
    if db["channel_link"]:
        return kb([Btn(text="🎃 Відкрити канал з костюмами", url=db["channel_link"])])
    return None


def main_kb() -> InlineKeyboardMarkup:
    rows = [
        [Btn(text="📸 Подати свій костюм", callback_data="sub")],
        [Btn(text="📋 Мої костюми", callback_data="my"), Btn(text="🗳 Мої голоси", callback_data="myv")],
    ]
    if db["channel_link"]:
        rows.append([Btn(text="🎃 Канал з костюмами", url=db["channel_link"])])
    return kb(*rows)


def nominations_kb(noms: list[str]) -> InlineKeyboardMarkup:
    return kb(*[[Btn(text=NOMS[k]["name"], callback_data=f"nom:{k}")] for k in noms])


def no_username_kb() -> InlineKeyboardMarkup:
    return kb([Btn(text="🙅 У партнера нема username", callback_data="nouser")])


def publish_kb() -> InlineKeyboardMarkup:
    return kb([
        Btn(text="✅ Опублікувати", callback_data="pub"),
        Btn(text="❌ Скасувати", callback_data="cancel"),
    ])


def my_entry_kb(e: dict) -> InlineKeyboardMarkup:
    left = settings.max_replaces - e.get("replaced", 0)
    return kb([
        Btn(text=f"🔄 Замінити фото ({left})", callback_data=f"rp:{e['num']}"),
        Btn(text="🗑 Зняти", callback_data=f"wd:{e['num']}"),
    ])


def withdraw_confirm_kb(num: int) -> InlineKeyboardMarkup:
    return kb([
        Btn(text="✅ Так, зняти", callback_data=f"wdy:{num}"),
        Btn(text="↩️ Ні", callback_data=f"wdn:{num}"),
    ])


def admin_mod_kb(num: int) -> InlineKeyboardMarkup:
    return kb([
        Btn(text="🗑 Видалити", callback_data=f"del:{num}"),
        Btn(text="🚫 Видалити + бан", callback_data=f"ban:{num}"),
    ])


def partner_approve_kb(num: int, uid: int) -> InlineKeyboardMarkup:
    return kb([
        Btn(text="✅ Так, це мій партнер", callback_data=f"pa:{num}:{uid}"),
        Btn(text="❌ Ні", callback_data=f"pr:{num}:{uid}"),
    ])


def pair_confirm_kb(num: int) -> InlineKeyboardMarkup:
    return kb([
        Btn(text="✅ Так, це я", callback_data=f"pc:{num}"),
        Btn(text="❌ Це не я", callback_data=f"pd:{num}"),
    ])
