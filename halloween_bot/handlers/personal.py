"""«Мої костюми» (зняти / замінити фото) та «Мої голоси»."""
from __future__ import annotations

from aiogram import Bot, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import settings
from ..domain.entries import active, is_participant, own_entry
from ..services.moderation import remove_entry
from ..services.notify import admin_action, notify_admins_photo, notify_admins_text
from ..services.publisher import queue_position, refresh_post
from ..storage import db, store
from ..ui.keyboards import main_kb, my_entry_kb, withdraw_confirm_kb
from ..ui.texts import caption, confirm_link, entry_label, esc, my_votes_text, post_link
from ..services.messaging import answer_long, set_buttons
from .filters import private_router
from .states import Sub
from .submit import check_photo

router = private_router("personal")

STATUS_TEXT = {"failed": "⚠️ збій публікації, адміни в курсі",
               "waiting": "⏳ чекає, поки партнер підтвердить участь, потім з'явиться в каналі"}


async def show_my(bot: Bot, chat_id: int, user) -> None:
    es = [e for e in db["entries"].values() if active(e) and is_participant(e, user)]
    if not es:
        await bot.send_message(chat_id, "У тебе ще нема костюмів у конкурсі.", reply_markup=main_kb())
        return
    for e in sorted(es, key=lambda e: e["num"]):
        is_own = e["owner"] == user.id
        lines = [caption(e), ""]
        if e["status"] == "published":
            lines.append(f"🔗 <a href=\"{post_link(e)}\">Пост у каналі</a>")
        elif e["status"] == "queued":
            pos, eta = queue_position(e["num"])
            lines.append(f"⏳ у черзі на публікацію: {pos}-й, ~{max(eta, 5)} с")
        else:
            lines.append(STATUS_TEXT.get(e["status"], e["status"]))
        if not is_own:
            lines.append("Ти тут партнер. Керує заявкою автор.")
        elif e["nom"] == "pair" and not e.get("partner_id"):
            lines.append(f"👥 Партнер ще не підтвердив. Перешли йому: {confirm_link(e['num'])}")
        await bot.send_photo(chat_id, e["photo"], caption="\n".join(lines),
                             reply_markup=my_entry_kb(e) if is_own else None)


@router.message(Command("my"))
async def cmd_my(m: Message, bot: Bot):
    await show_my(bot, m.chat.id, m.from_user)


@router.callback_query(F.data == "my")
async def cb_my(c: CallbackQuery, bot: Bot):
    await c.answer()
    await show_my(bot, c.message.chat.id, c.from_user)


@router.message(Command("myvotes"))
async def cmd_myvotes(m: Message):
    await answer_long(m, my_votes_text(m.from_user.id), reply_markup=main_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "myv")
async def cb_myvotes(c: CallbackQuery):
    await c.answer()
    await answer_long(c.message, my_votes_text(c.from_user.id), reply_markup=main_kb(), disable_web_page_preview=True)


def _num(c: CallbackQuery) -> str:
    return c.data.split(":")[1]


@router.callback_query(F.data.regexp(r"^wd:\d+$"))
async def withdraw_ask(c: CallbackQuery):
    e = own_entry(_num(c), c.from_user.id)
    if not e:
        return await c.answer("Заявку вже знято", show_alert=True)
    await set_buttons(c.message, withdraw_confirm_kb(e["num"]))
    await c.answer("Точно? Усі голоси за цей костюм пропадуть.", show_alert=True)


@router.callback_query(F.data.regexp(r"^wdn:\d+$"))
async def withdraw_no(c: CallbackQuery):
    e = own_entry(_num(c), c.from_user.id)
    if e:
        await set_buttons(c.message, my_entry_kb(e))
    await c.answer()


@router.callback_query(F.data.regexp(r"^wdy:\d+$"))
async def withdraw_yes(c: CallbackQuery, bot: Bot):
    e = own_entry(_num(c), c.from_user.id)
    if not e:
        return await c.answer("Заявку вже знято", show_alert=True)
    await remove_entry(bot, e["num"], by="self")
    await admin_action(bot, c.from_user, "автор сам зняв", entry_label(e), notify=False)
    await set_buttons(c.message, None)
    await c.message.answer(f"🗑 Костюм №{e['num']} знято з конкурсу. Можеш подати новий.",
                           reply_markup=main_kb())
    await notify_admins_text(bot, f"↩️ {esc(e['owner_tag'])} сам зняв костюм №{e['num']}.",
                             disable_notification=True)
    await c.answer()


@router.callback_query(F.data.regexp(r"^rp:\d+$"))
async def replace_ask(c: CallbackQuery, state: FSMContext):
    e = own_entry(_num(c), c.from_user.id)
    if not e:
        return await c.answer("Заявку вже знято", show_alert=True)
    if not db["submit_open"]:
        return await c.answer("Заміна фото доступна, поки відкритий прийом заявок.", show_alert=True)
    if e.get("replaced", 0) >= settings.max_replaces:
        return await c.answer(f"Ліміт замін ({settings.max_replaces}) вичерпано.", show_alert=True)
    await state.clear()
    await state.set_state(Sub.replace)
    await state.update_data(num=e["num"])
    await c.message.answer(f"📸 Надішли нове фото для №{e['num']}. Голоси збережуться.\n\n/cancel щоб скасувати")
    await c.answer()


@router.message(Sub.replace, F.photo)
async def replace_photo(m: Message, state: FSMContext, bot: Bot):
    num = (await state.get_data())["num"]
    e = own_entry(num, m.from_user.id)
    if not e:
        await state.clear()
        return await m.answer("Заявку вже знято.", reply_markup=main_kb())
    if not db["submit_open"]:
        await state.clear()
        return await m.answer("⏳ Прийом заявок закрито, заміна вже недоступна.", reply_markup=main_kb())
    res = await check_photo(bot, m, exclude=num)
    if res is None:
        return
    await state.clear()
    e["photo"] = m.photo[-1].file_id
    e["photo_uid"], e["phash"], e["similar_to"] = res
    e["replaced"] = e.get("replaced", 0) + 1
    store.save()
    await refresh_post(bot, e, new_photo=True)
    await m.answer(f"✅ Фото №{num} замінено! Залишилось замін: {settings.max_replaces - e['replaced']}",
                   reply_markup=main_kb())
    await notify_admins_photo(bot, e, header="🔄 <b>Замінено фото</b>\n")


@router.message(Sub.replace)
async def replace_bad(m: Message):
    await m.answer("Потрібне саме фото 📸 або /cancel")
