"""Звіти: статистика, результати, стан, аудит, журнали, бекап."""
from __future__ import annotations

import time

from aiogram import Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from ..domain.entries import get
from ..services import reports
from ..services.backups import send_backup
from ..services.messaging import answer_long
from ..storage import store
from ..ui.texts import esc
from .filters import IsAdmin, IsOwner, private_router

router = private_router("reports")


def _int_arg(command: CommandObject, default: int) -> int:
    a = (command.args or "").strip()
    return int(a) if a.isdigit() else default


@router.message(Command("stats"), IsAdmin)
async def cmd_stats(m: Message):
    await answer_long(m, reports.stats_text())


@router.message(Command("results"), IsAdmin)
async def cmd_results(m: Message, command: CommandObject, bot: Bot):
    await answer_long(m, await reports.results_text(bot, _int_arg(command, 5)))


@router.message(Command("health"), IsAdmin)
async def cmd_health(m: Message, bot: Bot):
    await answer_long(m, await reports.health_text(bot))


@router.message(Command("backup"), IsAdmin)
async def cmd_backup(m: Message, bot: Bot):
    await send_backup(bot, m.chat.id)


@router.message(Command("adminlog"), IsOwner)
async def cmd_adminlog(m: Message, command: CommandObject):
    text = reports.adminlog_text(_int_arg(command, 30))
    if text is None:
        return await m.answer("📝 Журнал дій адмінів порожній.")
    if len(text) > 3500:
        return await m.answer_document(BufferedInputFile(text.encode(), "adminlog.txt"),
                                       caption="📝 Журнал дій адмінів")
    await m.answer("📝 <b>Журнал дій адмінів</b>\n\n" + esc(text))


@router.message(Command("audit"), IsAdmin)
async def cmd_audit(m: Message, command: CommandObject):
    arg = (command.args or "").strip().lstrip("№")
    if not arg:
        return await answer_long(m, reports.audit_overview())
    e = get(arg) if arg.isdigit() else None
    if not e:
        return await m.answer("Використання: /audit або /audit 47")
    head, vlines = reports.audit_entry(e)
    if len(vlines) <= 40:
        await answer_long(m, head + "\n\n<b>Хто голосує:</b>\n" + esc("\n".join(vlines) or "нікого"))
    else:
        await m.answer(head + "\n\nСписок голосуючих у файлі 👇")
        await m.answer_document(BufferedInputFile("\n".join(vlines).encode(), f"audit_{e['num']}.txt"))


@router.message(Command("votelog"), IsAdmin)
async def cmd_votelog(m: Message):
    await m.answer_document(BufferedInputFile(reports.votelog_csv(), f"votelog_{time.strftime('%H-%M')}.csv"),
                            caption=f"📒 Журнал голосів: {len(store.vlog)} записів")
