"""Розсилки: /broadcast (всім) і /remind (персонально, скільки голосів лишилось)."""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import settings
from ..services.messaging import mass_send
from ..services.notify import admin_action
from ..services.reports import remind_jobs
from ..storage import db
from ..ui.keyboards import channel_kb
from ..ui.texts import reminder_text
from .filters import IsAdmin, private_router

router = private_router("broadcast")


def _eta(n: int) -> int:
    return int(n / settings.send_rate) + 1


@router.message(Command("broadcast"), IsAdmin)
async def cmd_broadcast(m: Message, command: CommandObject, bot: Bot):
    if not command.args:
        return await m.answer("Використання: /broadcast текст")
    await admin_action(bot, m.from_user, "розсилка всім", command.args[:200])
    users = [u for u in db["users"] if u not in db["banned"]]
    await m.answer(f"📣 Розсилаю {len(users)} людям (~{_eta(len(users))} с)…")
    ok, bad = await mass_send(bot, [(uid, command.args, {"parse_mode": None}) for uid in users])
    await m.answer(f"📣 Надіслано: {ok}/{len(users)}" + (f" · не доставлено: {bad}" if bad else ""))


@router.message(Command("remind"), IsAdmin)
async def cmd_remind(m: Message, command: CommandObject, bot: Bot):
    if not db["vote_open"]:
        return await m.answer("Голосування закрите, нагадувати нема про що.")
    extra = (command.args or "").strip()
    await admin_action(bot, m.from_user, "персональні нагадування", extra[:200], notify=False)
    jobs, skipped = remind_jobs(extra, reminder_text, channel_kb())
    await m.answer(f"📨 Розсилаю нагадування {len(jobs)} людям (~{_eta(len(jobs))} с)…")
    sent, failed = await mass_send(bot, jobs)
    await m.answer(f"✅ Надіслано: {sent}\n⏭ Усі голоси вже використали: {skipped}\n"
                   f"❌ Не доставлено (заблокували бота або збій мережі): {failed}")
