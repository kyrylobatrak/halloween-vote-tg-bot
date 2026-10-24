"""Видалення, бан, відновлення, повторна публікація, перевірка постів."""
from __future__ import annotations

from aiogram import Bot, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..domain.entries import get
from ..domain.people import is_admin, name_of, tag_of
from ..services.moderation import ban_refusal, remove_entry, restore_entry, unban_user, verify_all
from ..services.notify import admin_action
from ..services.publisher import enqueue
from ..storage import db, store
from ..ui.texts import entry_label, esc
from .filters import IsAdmin, private_router

router = private_router("moderation")


@router.callback_query(F.data.regexp(r"^(del|ban):\d+$"))
async def mod_cb(c: CallbackQuery, bot: Bot):
    """Кнопки 🗑 / 🚫 під копією заявки в адміна."""
    if not is_admin(c.from_user.id):
        return await c.answer()
    act, n = c.data.split(":")
    if act == "ban" and (why := ban_refusal(int(n))):
        return await c.answer(why[:200], show_alert=True)
    ok = await remove_entry(bot, int(n), ban=(act == "ban"))
    if not ok:
        return await c.answer("Вже видалено", show_alert=True)
    await admin_action(bot, c.from_user, "видалив і забанив автора" if act == "ban" else "видалив",
                       entry_label(get(n)) + f" · /restore {n}", alert=True)
    mark = ("🚫 Видалено + бан" if act == "ban" else "🗑 Видалено") + f" · /restore {n}"
    try:
        await c.message.edit_caption(caption=f"{c.message.html_text}\n\n<b>{mark}</b> ({esc(tag_of(c.from_user))})",
                                     reply_markup=None)
    except TelegramBadRequest:
        pass
    await c.answer(mark)


@router.message(Command("del", "ban"), IsAdmin)
async def cmd_del(m: Message, command: CommandObject, bot: Bot):
    arg = (command.args or "").strip().lstrip("№")
    if not arg.isdigit():
        return await m.answer(f"Використання: /{command.command} 47")
    num = int(arg)
    if command.command == "ban" and (why := ban_refusal(num)):
        return await m.answer(why)
    ok = await remove_entry(bot, num, ban=(command.command == "ban"))
    if ok:
        await admin_action(bot, m.from_user,
                           "видалив і забанив автора" if command.command == "ban" else "видалив",
                           entry_label(get(num)) + f" · /restore {num}", alert=True)
    await m.answer(f"{'✅ Готово' if ok else 'Не знайдено або вже видалено'}: №{num}"
                   + (f"\nПомилився? /restore {num}" if ok else ""))


@router.message(Command("unban"), IsAdmin)
async def cmd_unban(m: Message, command: CommandObject, bot: Bot):
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("Використання: /unban 123456789")
    uid = int(command.args.strip())
    if uid not in db["banned"]:
        return await m.answer(f"{uid} не забанений")
    back, skipped = unban_user(uid)
    await admin_action(bot, m.from_user, "розбанив", f"{name_of(uid)} · голосів повернуто {back}")
    await m.answer(f"✅ {uid} розбанено. Його голосів повернуто: {back}"
                   + (f" (не вдалося {skipped}: костюми знято або ліміт)" if skipped else ""))


@router.message(Command("repost"), IsAdmin)
async def cmd_repost(m: Message, command: CommandObject, bot: Bot):
    """Збій публікації → ще раз. Пара, що чекає партнера (сів телефон, нема Telegram) → опублікувати без нього."""
    e = get((command.args or "").strip())
    if not e or e["status"] not in ("failed", "waiting"):
        return await m.answer("Немає такої заявки зі статусом «збій» чи «чекає партнера»")
    forced = e["status"] == "waiting"
    e["status"] = "queued"
    store.save()
    await admin_action(bot, m.from_user, "опублікував пару без підтвердження" if forced else "повторна публікація",
                       entry_label(e), notify=forced)
    await enqueue(e["num"])
    await m.answer(f"🔁 №{e['num']} у черзі на публікацію" + (" (без підтвердження партнера)" if forced else ""))


@router.message(Command("restore"), IsAdmin)
async def cmd_restore(m: Message, command: CommandObject, bot: Bot):
    res = await restore_entry(bot, m.from_user, command.args or "")
    await m.answer(res.text)


@router.message(Command("verify"), IsAdmin)
async def cmd_verify(m: Message, bot: Bot):
    n = sum(1 for e in db["entries"].values() if e["status"] == "published")
    await m.answer(f"🔍 Перевіряю {n} постів у каналі…")
    dropped = await verify_all(bot)
    if dropped:
        await m.answer("🗑 Видалені з каналу вручну, знято з конкурсу разом з голосами: "
                       + ", ".join(f"№{n}" for n in dropped))
    else:
        await m.answer("✅ Усі пости на місці.")
