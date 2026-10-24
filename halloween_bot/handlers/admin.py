"""Керування конкурсом: адміни, канал, відкриття/закриття, тихий режим."""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..domain.people import name_of, resolve_user, username_of
from ..services.commands import sync_commands
from ..services.channel import make_invite_link
from ..services.messaging import answer_long, channel_slot, safe_send
from ..services.notify import admin_action
from ..storage import db, store
from ..ui.texts import ADMIN_HELP, channel_announce, esc
from .filters import IsAdmin, IsOwner, private_router

router = private_router("admin")

TOGGLE_REPLY = {
    ("submit_open", True): "✅ Прийом костюмів відкрито",
    ("submit_open", False): "⛔ Прийом костюмів закрито",
    ("vote_open", True): "✅ Голосування відкрито",
    ("vote_open", False): "⛔ Голосування закрито",
}


@router.message(Command("admin"), IsAdmin)
async def cmd_admin(m: Message):
    await m.answer(ADMIN_HELP)


@router.message(Command("addadmin", "deladmin"), IsOwner)
async def cmd_addadmin(m: Message, command: CommandObject, bot: Bot):
    if not command.args:
        return await m.answer(f"Використання: /{command.command} @username")
    uid = resolve_user(command.args)
    who = esc(command.args.strip())
    if uid is None:
        return await m.answer(f"Не знаю {who} 🤷\nХай ця людина спершу натисне /start у боті, потім повтори.")
    if command.command == "addadmin":
        if uid not in db["admins"]:
            db["admins"].append(uid)
            store.save()
            await admin_action(bot, m.from_user, "додав адміна", name_of(uid), notify=False)
        await m.answer(f"✅ {who} тепер адмін")
        await safe_send(bot, uid, "🛡 Тебе зробили адміном конкурсу костюмів. Команди: /admin")
        await sync_commands(bot)
    else:
        if uid in db["admins"]:
            db["admins"].remove(uid)
            store.save()
            await admin_action(bot, m.from_user, "прибрав адміна", name_of(uid), notify=False)
        await m.answer(f"✅ {who} більше не адмін")
        await sync_commands(bot, removed=None if uid == db["owner"] else uid)


@router.message(Command("admins"), IsAdmin)
async def cmd_admins(m: Message):
    def show(uid: int) -> str:
        u = username_of(uid)
        return f"@{u}" if u else f"<code>{uid}</code>"
    lines = [f"👑 {show(db['owner'])}"] + [f"🛡 {show(a)}" for a in db["admins"]]
    ch = "✅ підключено" if db["channel_id"] else "❌ не підключено (додай бота адміном у канал)"
    await answer_long(m, "\n".join(lines) + f"\n\nКанал: {ch}")


@router.message(Command("quiet"), IsAdmin)
async def cmd_quiet(m: Message):
    q = db["quiet_admins"]
    if m.from_user.id in q:
        q.remove(m.from_user.id)
        text = "🔔 Тепер ти отримуєш копію кожної заявки."
    else:
        q.append(m.from_user.id)
        text = ("🔕 Тихий режим: присилатиму тільки підозрілі заявки (схожі фото), "
                "відмови партнерів і збої. Вимкнути: /quiet")
    store.save()
    await m.answer(text)


@router.message(Command("newlink"), IsOwner)
async def cmd_newlink(m: Message, bot: Bot):
    """Якщо посилання все ж перестало працювати — створити нове (старі не анулюються)."""
    if not db["channel_id"]:
        return await m.answer("Канал не підключено.")
    link, mode = await make_invite_link(bot, db["channel_id"])
    if not link:
        return await m.answer("⚠️ Не вийшло. Перевір, що бот має право «Запрошувати користувачів».")
    db["channel_link"], db["channel_link_mode"] = link, mode
    store.save()
    await m.answer(f"🔗 Нове посилання на канал: {link}\nКнопки в боті вже ведуть на нього.")


@router.message(Command("open_submit", "close_submit", "open_vote", "close_vote"), IsAdmin)
async def cmd_toggle(m: Message, command: CommandObject, bot: Bot):
    key = "submit_open" if "submit" in command.command else "vote_open"
    val = command.command.startswith("open")
    db[key] = val
    store.save()
    await admin_action(bot, m.from_user, f"/{command.command}")
    await m.answer(TOGGLE_REPLY[(key, val)])
    if not db["channel_id"]:
        return await m.answer("⚠️ Канал ще не підключено: додай бота адміном у канал.")
    try:
        await channel_slot()
        await bot.send_message(db["channel_id"], channel_announce(key, val))
    except Exception as ex:
        await m.answer(f"⚠️ Не вдалося написати в канал: {esc(str(ex))}")
