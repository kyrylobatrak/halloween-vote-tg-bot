"""Лише гості вечірки: чужі з пересланим посиланням на бота чи канал не подають і не голосують."""
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import ApproveChatJoinRequest, DeclineChatJoinRequest

from conftest import ADMIN2, ALICE, BOBBY, CAROL, OWNER, PARTY, TG, run
from halloween_bot.config import settings
from halloween_bot.runtime import rt
from halloween_bot.storage import db

STRANGER = TG.user(77, "random_guy")


async def party_contest(tg, bot_status: str = "administrator"):
    """Канал підключено, потім власник додав бота в чат туси. Аліса й Боббі — гості."""
    await tg.setup_contest(OWNER)
    await tg.party_bot(OWNER, bot_status)
    await tg.guest(ALICE["id"], BOBBY["id"])
    await tg.member(ALICE["id"], BOBBY["id"], STRANGER["id"])     # чужий якось пробрався в канал
    settings.tap_guard = 0


def test_connect_party_chat_switches_channel_link_to_requests(tg):
    async def go():
        await tg.setup_contest(OWNER)
        old = db["channel_link"]
        assert tg.fake.links[old] is False and db["channel_link_mode"] == "open"
        await tg.party_bot(OWNER)
        assert db["party_chat_id"] == PARTY
        new = db["channel_link"]
        assert new != old and tg.fake.links[new] is True and db["channel_link_mode"] == "request"
        assert old in tg.fake.revoked                         # старе відкрите посилання більше не пускає
        text = tg.fake.last_to(OWNER["id"])
        assert "Чат туси" in text and "за заявкою" in text and "адміном" not in text
    run(go())


def test_party_chat_first_then_channel_link_is_request_from_start(tg):
    async def go():
        await tg.send(OWNER, "/start")
        await tg.send(OWNER, "/owner")
        await tg.party_bot(OWNER)
        await tg.bot_status(OWNER, "administrator")
        assert db["channel_link_mode"] == "request" and tg.fake.links[db["channel_link"]] is True
        assert not tg.fake.revoked
    run(go())


def test_stranger_cannot_submit_vote_or_be_partner(tg):
    async def go():
        await party_contest(tg)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        assert n
        assert "лише для гостей" in await tg.tap(STRANGER, "sub")
        assert "лише для гостей" in await tg.vote(STRANGER, n)
        assert not db["votes"].get(str(STRANGER["id"]))
        # Боббі (гість) подає пару з чужим — чужий не може підтвердитись
        p = await tg.submit_pair(BOBBY, "@random_guy", "b1", "Пара")
        await tg.tap(STRANGER, f"pc:{p}")
        assert db["entries"][str(p)]["partner_id"] is None
        assert "лише для гостей" in tg.fake.last_to(STRANGER["id"])
        await tg.send(STRANGER, f"/start p{p}")
        assert db["entries"][str(p)]["partner_id"] is None
        assert "зараховано" in await tg.vote(BOBBY, n)          # гість голосує нормально
    run(go())


def test_stranger_added_to_party_chat_gets_access_and_leaving_takes_it_away(tg):
    async def go():
        await party_contest(tg)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        assert "лише для гостей" in await tg.vote(STRANGER, n)
        await tg.guest(STRANGER["id"])                           # орги додали в чат
        assert "зараховано" in await tg.vote(STRANGER, n)
        await tg.guest(STRANGER["id"], status="left")            # вийшов з чату туси
        assert "лише для гостей" in await tg.vote(STRANGER, n)
    run(go())


def test_join_requests_to_channel_guests_in_strangers_out(tg):
    async def go():
        await party_contest(tg)
        await tg.join_request(CAROL)                              # не гість
        await tg.join_request(ALICE)                              # гість
        await tg.join_request(ADMIN2)                             # адмін без чату туси (ще не адмін)
        approved = {m.user_id for m in tg.fake.of(ApproveChatJoinRequest)}
        declined = {m.user_id for m in tg.fake.of(DeclineChatJoinRequest)}
        assert approved == {ALICE["id"]} and declined == {CAROL["id"], ADMIN2["id"]}
        assert "лише для гостей" in tg.fake.last_to(CAROL["id"])
        await tg.send(ADMIN2, "/start")
        await tg.send(OWNER, "/addadmin @adminka")
        await tg.join_request(ADMIN2)
        assert ADMIN2["id"] in {m.user_id for m in tg.fake.of(ApproveChatJoinRequest)}
    run(go())


def test_admins_and_owner_pass_without_party_chat(tg):
    async def go():
        await party_contest(tg)
        await tg.send(ADMIN2, "/start")
        await tg.send(OWNER, "/addadmin @adminka")
        n = await tg.submit_solo(OWNER, "o1", "Дракула")          # власника нема в чаті туси
        assert n and db["entries"][str(n)]["status"] == "published"
        await tg.member(ADMIN2["id"])
        assert "зараховано" in await tg.vote(ADMIN2, n)
    run(go())


def test_bot_as_plain_member_warns_and_still_checks_via_api(tg):
    async def go():
        await party_contest(tg, bot_status="member")
        assert "адміном" in tg.fake.last_to(OWNER["id"])
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        tg.fake.party[CAROL["id"]] = "member"                     # події нема — бот питає Telegram сам
        await tg.member(CAROL["id"])
        assert "зараховано" in await tg.vote(CAROL, n)
    run(go())


def test_telegram_error_on_check_is_reported_not_silently_allowed(tg):
    async def go():
        await party_contest(tg)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")

        def fail(m):
            if m.chat_id == PARTY:
                raise TelegramNetworkError(method=m, message="timeout")
        tg.fake.hooks["GetChatMember"] = fail
        await tg.member(CAROL["id"])
        assert "Не вдалося перевірити" in await tg.vote(CAROL, n)
        assert not db["votes"].get(str(CAROL["id"]))
    run(go())


def test_stranger_adding_bot_to_group_is_ignored_and_removal_disables_gate(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.party_bot(STRANGER)
        assert db["party_chat_id"] is None
        await tg.party_bot(OWNER)
        assert db["party_chat_id"] == PARTY
        await tg.party_bot(OWNER, "left")
        assert db["party_chat_id"] is None and "вимкнено" in tg.fake.last_to(OWNER["id"])
        n = await tg.submit_solo(CAROL, "c1", "Зомбі")            # перевірки нема — як раніше
        assert n
    run(go())


def test_party_status_in_health(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(OWNER, "/health")
        assert "Чат туси: ❌" in tg.fake.last_to(OWNER["id"])
        await tg.party_bot(OWNER)
        await tg.send(OWNER, "/health")
        assert "Чат туси: ✅" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_party_cache_is_cleared_on_restart_but_settings_persist(tg):
    async def go():
        await party_contest(tg)
        assert rt.party_cache[ALICE["id"]][0] is True
        from halloween_bot.storage import store
        assert await store.flush()
        store.load()
        rt.reset()
        assert db["party_chat_id"] == PARTY and db["channel_link_mode"] == "request"
    run(go())


SUPER = -1007777777777          # новий ID після перетворення групи на супергрупу


def _member_only_in(chat_id):
    """Фейковий getChatMember: учасники є лише в чаті chat_id (стара група вже «мертва»)."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import ChatMemberMember, User

    def hook(m):
        if m.chat_id in (PARTY, SUPER):
            if m.chat_id != chat_id:
                raise TelegramBadRequest(method=m, message="Bad Request: chat not found")
            return ChatMemberMember(user=User(id=m.user_id, is_bot=False, first_name="U"))
    return hook


def test_group_migrated_to_supergroup_by_service_message(tg):
    """Telegram сам перетворив чат туси на супергрупу — бот переходить на новий ID і далі пускає гостей."""
    import time as _t

    async def go():
        await party_contest(tg)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg._feed({"message": {"message_id": 1, "date": int(_t.time()),
                                    "chat": {"id": PARTY, "type": "group", "title": "Туса"},
                                    "from": OWNER, "migrate_to_chat_id": SUPER}})
        assert db["party_chat_id"] == SUPER
        tg.fake.hooks["GetChatMember"] = _member_only_in(SUPER)
        assert "зараховано" in await tg.vote(BOBBY, n)
        # повідомлення «створено з групи» з боку супергрупи — нічого не ламає
        await tg._feed({"message": {"message_id": 2, "date": int(_t.time()),
                                    "chat": {"id": SUPER, "type": "supergroup", "title": "Туса"},
                                    "from": OWNER, "migrate_from_chat_id": PARTY}})
        assert db["party_chat_id"] == SUPER
    run(go())


def test_group_migration_detected_on_member_check(tg):
    """Службове повідомлення бот пропустив (був вимкнений) — Telegram сам скаже новий ID на першій перевірці."""
    from aiogram.exceptions import TelegramMigrateToChat

    async def go():
        await party_contest(tg)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        rt.party_cache.clear()
        seen = []

        def migrated(m):
            seen.append(m.chat_id)
            if m.chat_id == PARTY:
                raise TelegramMigrateToChat(method=m, message="group chat was upgraded to a supergroup chat",
                                            migrate_to_chat_id=SUPER)
            if m.chat_id == SUPER:
                return __import__("aiogram").types.ChatMemberMember(
                    user=__import__("aiogram").types.User(id=m.user_id, is_bot=False, first_name="U"))
        tg.fake.hooks["GetChatMember"] = migrated
        assert "зараховано" in await tg.vote(BOBBY, n)
        assert db["party_chat_id"] == SUPER and seen[:2] == [PARTY, SUPER]
    run(go())
