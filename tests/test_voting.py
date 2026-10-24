"""Голосування кнопкою під постом у каналі."""
import asyncio

from aiogram.methods import GetChatMember

from conftest import ALICE, BOBBY, EVE, NONAME, OWNER, VOTER, run
from halloween_bot.config import settings
from halloween_bot.storage import db, store


async def contest_with_entries(tg, n_solo=3):
    await tg.setup_contest(OWNER)
    users = [tg.user(200 + i) for i in range(n_solo)]
    for i, u in enumerate(users):
        await tg.submit_solo(u, f"s{i}", f"Соло {i}")
    return users


def test_non_member_refused_member_votes(tg):
    async def go():
        await contest_with_entries(tg)
        assert "лише підписники" in await tg.vote(VOTER, 1)
        await tg.member(VOTER["id"])
        assert "зараховано" in await tg.vote(VOTER, 1)
        assert db["votes"][str(VOTER["id"])]["solo"] == [1]
    run(go())


def test_known_member_votes_without_api_call(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.member(VOTER["id"])
        before = len(tg.fake.of(GetChatMember))
        await tg.vote(VOTER, 1)
        assert len(tg.fake.of(GetChatMember)) == before, "підписник з події каналу — без запиту до Telegram"
    run(go())


def test_unknown_user_checked_once_then_event_wins(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.vote(VOTER, 1)
        await tg.vote(VOTER, 1)
        assert len([c for c in tg.fake.of(GetChatMember) if c.user_id == VOTER["id"]]) == 1
        tg.fake.members[VOTER["id"]] = "member"
        await tg.chat_member(VOTER["id"], "member")
        assert "зараховано" in await tg.vote(VOTER, 1)
        tg.fake.members[VOTER["id"]] = "left"
        await tg.chat_member(VOTER["id"], "left")
        assert "лише підписники" in await tg.vote(VOTER, 2)
    run(go())


def test_double_tap_guard_and_unvote(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.member(VOTER["id"])
        settings.tap_guard = 0.2
        assert "зараховано!" in await tg.vote(VOTER, 1)
        assert "уже зараховано" in await tg.vote(VOTER, 1)
        assert db["votes"][str(VOTER["id"])]["solo"] == [1]
        await asyncio.sleep(0.25)
        assert "знято" in await tg.vote(VOTER, 1)
        assert db["votes"][str(VOTER["id"])]["solo"] == []
        assert "уже знято" in await tg.vote(VOTER, 1)
    run(go())


def test_concurrent_taps_count_once(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.member(VOTER["id"])
        await asyncio.gather(tg.vote(VOTER, 2), tg.vote(VOTER, 2), tg.vote(VOTER, 2))
        assert db["votes"][str(VOTER["id"])]["solo"] == [2]
    run(go())


def test_vote_limit_per_nomination(tg):
    async def go():
        await contest_with_entries(tg, n_solo=9)
        await tg.member(VOTER["id"])
        settings.tap_guard = 0
        for n in range(1, 8):
            assert "зараховано" in await tg.vote(VOTER, n)
        alert = await tg.vote(VOTER, 8)
        assert "Ліміт 7" in alert and len(alert) <= 200
        assert "знято" in await tg.vote(VOTER, 3)
        assert "зараховано" in await tg.vote(VOTER, 8)
    run(go())


def test_cannot_vote_for_own_or_own_pair(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(BOBBY, "/start")
        await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Пара")
        await tg.submit_pair(EVE, None, "pp2", "Пара 2")
        await tg.send(NONAME, "/start p2")
        await tg.tap(EVE, f"pa:2:{NONAME['id']}")
        await tg.member(ALICE["id"], BOBBY["id"], NONAME["id"])
        assert "ще не опубліковано" in await tg.vote(ALICE, 1)        # Боббі ще не підтвердив
        await tg.tap(BOBBY, "pc:1")
        await tg.drain()
        assert "За свій" in await tg.vote(ALICE, 1)
        assert "За свій" in await tg.vote(BOBBY, 1)
        assert "За свій" in await tg.vote(NONAME, 2), "партнер без username після підтвердження"
    run(go())


def test_closed_voting_and_removed_entry(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.member(VOTER["id"])
        db["vote_open"] = False
        assert "закрите" in await tg.vote(VOTER, 1)
        db["vote_open"] = True
        db["entries"]["1"]["status"] = "removed"
        assert "знято з конкурсу" in await tg.vote(VOTER, 1)
    run(go())


def test_entry_removed_during_membership_check(tg):
    async def go():
        await contest_with_entries(tg)

        def remove_while_checking(method):
            db["entries"]["2"]["status"] = "removed"
            return None                                   # далі — звичайна відповідь
        tg.fake.hooks["GetChatMember"] = remove_while_checking
        tg.fake.members[VOTER["id"]] = "member"
        assert "щойно знято" in await tg.vote(VOTER, 2)
        assert 2 not in db["votes"].get(str(VOTER["id"]), {}).get("solo", [])
    run(go())


def test_membership_check_failure_is_reported(tg):
    async def go():
        await contest_with_entries(tg)

        def boom(method):
            raise ConnectionError("hotspot")
        tg.fake.hooks["GetChatMember"] = boom
        assert "Не вдалося перевірити" in await tg.vote(VOTER, 1)
    run(go())


def test_votes_written_to_journal(tg):
    async def go():
        await contest_with_entries(tg)
        await tg.member(VOTER["id"])
        settings.tap_guard = 0
        await tg.vote(VOTER, 1)
        await tg.vote(VOTER, 1)
        assert [r[4] for r in store.vlog] == [1, -1]
        assert await store.flush()
        lines = settings.votes_log_path.read_text().splitlines()
        assert len(lines) == 2
    run(go())


def test_require_member_can_be_disabled(tg):
    async def go():
        await contest_with_entries(tg)
        settings.require_member = False
        assert "зараховано" in await tg.vote(VOTER, 1)
    run(go())
