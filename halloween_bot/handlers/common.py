"""Старт, скасування, власник, альбоми, підтвердження партнера."""
from __future__ import annotations

import logging
import re
import time

from aiogram import Bot, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import append_env, settings
from ..domain.people import is_owner, note_user, remember, tag_of
from ..runtime import rt
from ..storage import db, store
from ..services.partners import answer_request, confirm_partner, deny_partner
from ..ui.keyboards import main_kb
from ..ui.texts import OWNER_GREETING, cancel_text, welcome
from ..services.channel import connect_pending
from ..services.commands import sync_commands
from ..services.party import NOT_GUEST, party_gate
from ..services.messaging import set_buttons
from .filters import private_router

log = logging.getLogger("halloween.handlers")
router = private_router("common")


@router.message(F.media_group_id)
async def album_guard(m: Message):
    """Альбом приходить як кілька окремих повідомлень, які обробляються паралельно.
    Перше пропускаємо далі, решту тихо ігноруємо (перевірка без await, тож без гонок)."""
    now = time.time()
    for g, t in list(rt.album_seen.items()):
        if now - t > 120:
            rt.album_seen.pop(g, None)
    if m.media_group_id in rt.album_seen:
        return
    rt.album_seen[m.media_group_id] = now
    raise SkipHandler


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, command: CommandObject, bot: Bot):
    await state.clear()
    remember(m.from_user)
    arg = (command.args or "").strip()
    if re.fullmatch(r"p\d+", arg):                 # посилання-підтвердження партнера
        return await m.answer(await confirm_partner(bot, m.from_user, int(arg[1:])), reply_markup=main_kb())
    await m.answer(welcome(), reply_markup=main_kb())
    if (why := await party_gate(bot, m.from_user)) == NOT_GUEST:      # чужий з пересланим посиланням
        await m.answer(why)
    if db["owner"] is None:
        await m.answer("👑 У бота ще нема власника. Якщо це твій бот, натисни /owner")


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await m.answer(cancel_text(await state.get_state()), reply_markup=main_kb())
    await state.clear()


@router.message(Command("myid"))
async def cmd_myid(m: Message):
    await m.answer(f"Твій user_id: <code>{m.from_user.id}</code>")


@router.message(Command("owner"))
async def cmd_owner(m: Message, bot: Bot):
    remember(m.from_user)
    if db["owner"] is None and not settings.owner_id:
        db["owner"] = m.from_user.id
        settings.owner_id = m.from_user.id
        store.save()
        try:                                  # закріплюємо в .env: навіть без data.json бота не перехоплять
            append_env("OWNER_ID", str(m.from_user.id))
        except OSError as ex:
            log.warning("не вдалося записати OWNER_ID у .env: %s", ex)
        log.info("Власник: %s (%s)", tag_of(m.from_user), m.from_user.id)
        await m.answer(OWNER_GREETING)
        await connect_pending(bot, m.from_user.id)     # бота вже додали в канал до /owner
        await sync_commands(bot)
    elif is_owner(m.from_user.id):
        await m.answer("Ти вже власник 👑 Команди: /admin")
    else:
        await m.answer("У бота вже є власник.")


@router.callback_query(F.data.regexp(r"^pc:\d+$"))
async def pair_confirm_cb(c: CallbackQuery, bot: Bot):
    note_user(c.from_user)
    text = await confirm_partner(bot, c.from_user, int(c.data[3:]))
    await set_buttons(c.message, None)
    await c.message.answer(text)
    await c.answer()


@router.callback_query(F.data.regexp(r"^pd:\d+$"))
async def pair_deny_cb(c: CallbackQuery, bot: Bot):
    await set_buttons(c.message, None)
    await c.message.answer("Ок, повідомив автору та адмінам 👌")
    await deny_partner(bot, c.from_user, int(c.data[3:]))
    await c.answer()


@router.callback_query(F.data.regexp(r"^p[ar]:\d+:\d+$"))
async def partner_request_cb(c: CallbackQuery, bot: Bot):
    """Автор пари схвалює (pa) або відхиляє (pr) запит партнера без username."""
    act, num, uid = c.data.split(":")
    text = await answer_request(bot, c.from_user, int(num), int(uid), approve=(act == "pa"))
    if text in ("Це не твоя пара.",):
        return await c.answer(text, show_alert=True)
    await set_buttons(c.message, None)
    await c.message.answer(text)
    await c.answer()
