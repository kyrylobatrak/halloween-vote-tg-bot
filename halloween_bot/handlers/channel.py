"""Події каналу й чату туси: бота додали / зробили адміном / прибрали; хтось вступив / вийшов;
заявка на вступ у канал."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, Message

from ..services.channel import on_bot_status
from ..services.membership import apply_member_event
from ..services.party import apply_party_event, migrate_party, on_join_request, on_party_bot_status

router = Router(name="channel")


@router.my_chat_member(F.chat.type == "channel")
async def on_channel(ev: ChatMemberUpdated, bot: Bot):
    await on_bot_status(bot, ev.chat, ev.from_user, ev.new_chat_member)


@router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_party_chat(ev: ChatMemberUpdated, bot: Bot):
    await on_party_bot_status(bot, ev.chat, ev.from_user, ev.new_chat_member)


@router.message(F.migrate_to_chat_id)
async def on_group_migrated(m: Message):
    """Службове повідомлення в старій групі: «групу перетворено на супергрупу»."""
    migrate_party(m.chat.id, m.migrate_to_chat_id)


@router.message(F.migrate_from_chat_id)
async def on_supergroup_created(m: Message):
    """Те саме, але з боку нової супергрупи."""
    migrate_party(m.migrate_from_chat_id, m.chat.id)


@router.chat_member()
async def on_member_change(ev: ChatMemberUpdated):
    apply_member_event(ev.chat.id, ev.new_chat_member) or apply_party_event(ev.chat.id, ev.new_chat_member)


@router.chat_join_request()
async def on_channel_join_request(req: ChatJoinRequest, bot: Bot):
    await on_join_request(bot, req)
