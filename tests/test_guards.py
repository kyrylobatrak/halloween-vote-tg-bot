"""Захисні перевірки, які мутаційне тестування знайшло непокритими: якби їх зламали,
жоден інший тест цього б не помітив."""
import asyncio

from aiogram.methods import SendDocument

from conftest import ADMIN2, ALICE, BOBBY, CAROL, EVE, NONAME, OWNER, VOTER, TG, run
from halloween_bot.config import settings
from halloween_bot.runtime import rt
from halloween_bot.storage import db

ADMIN_COMMANDS = ["/open_submit", "/close_submit", "/open_vote", "/close_vote", "/stats", "/results",
                  "/health", "/del 1", "/ban 1", "/restore 1", f"/unban {BOBBY['id']}", "/repost 1",
                  "/verify", "/quiet", "/audit", "/audit 1", "/votelog", "/broadcast хак", "/remind хак",
                  "/admin", "/backup", "/admins"]
OWNER_COMMANDS = ["/addadmin @voter_one", "/deladmin @adminka", "/adminlog", "/newlink"]


def snapshot() -> dict:
    return {"submit": db["submit_open"], "vote": db["vote_open"], "admins": list(db["admins"]),
            "banned": list(db["banned"]), "quiet": list(db["quiet_admins"]), "link": db["channel_link"],
            "entries": {k: e["status"] for k, e in db["entries"].items()}}


def test_regular_guest_cannot_use_any_admin_command_or_button(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ADMIN2, "/start")
        await tg.send(OWNER, "/addadmin @adminka")
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.send(VOTER, "/start")
        before, calls_before = snapshot(), len(tg.fake.calls)
        for cmd in ADMIN_COMMANDS + OWNER_COMMANDS:
            await tg.send(VOTER, cmd)
        for data in (f"del:{n}", f"ban:{n}"):
            await tg.tap(VOTER, data)
        assert snapshot() == before, "звичайний гість щось змінив адмін-командою"
        new = tg.fake.calls[calls_before:]
        assert not [c for c in new if isinstance(c, SendDocument)], "гість отримав бекап / журнал"
        to_others = [c for c in new if getattr(c, "chat_id", VOTER["id"]) not in (VOTER["id"],)]
        assert not to_others, f"команди гостя розіслали щось іншим: {to_others[:2]}"
        # адмін (не власник) — власницькі команди не працюють
        before = snapshot()
        for cmd in OWNER_COMMANDS:
            await tg.send(ADMIN2, cmd)
        assert snapshot() == before
    run(go())


def test_banned_person_cannot_be_named_or_join_a_pair(tg):
    async def go():
        await tg.setup_contest(OWNER)
        nb = await tg.submit_solo(BOBBY, "b1", "Мумія")
        await tg.send(OWNER, f"/ban {nb}")
        # вписати забаненого партнером — одразу пояснення
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:pair")
        await tg.send(ALICE, "@bobby_k")
        assert "не може брати участь" in tg.fake.last_to(ALICE["id"])
        # пара без username: забанений відкриває посилання — не стає партнером і не просить автора
        p = await tg.submit_pair(EVE, None, "e1", "Пара")
        await tg.send(BOBBY, f"/start p{p}")
        assert "🚫" in tg.fake.last_to(BOBBY["id"])
        e = db["entries"][str(p)]
        assert e["partner_id"] is None and not e.get("partner_requests")
    run(go())


def test_unban_never_returns_more_than_vote_limit(tg):
    """Голос за знятий №A «заморожується» в бані разом з іншими — розбан не дасть більше за ліміт."""
    async def go():
        settings.max_votes, settings.tap_guard = 2, 0
        await tg.setup_contest(OWNER)
        na = await tg.submit_solo(ALICE, "a", "A")
        nb = await tg.submit_solo(CAROL, "c", "B")
        nc = await tg.submit_solo(EVE, "e", "C")
        nv = await tg.submit_solo(VOTER, "v", "Костюм виборця")
        await tg.member(VOTER["id"])
        await tg.vote(VOTER, na)
        await tg.vote(VOTER, nb)
        await tg.send(OWNER, f"/del {na}")              # голос за A пропав
        assert "зараховано" in await tg.vote(VOTER, nc)  # добрав до 2
        await tg.send(OWNER, f"/ban {nv}")
        await tg.send(OWNER, f"/restore {na}")          # A повернули, поки виборець у бані
        await tg.send(OWNER, f"/unban {VOTER['id']}")
        assert len(db["votes"][str(VOTER["id"])]["solo"]) == 2
        assert "не вдалося 1" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_partner_vote_for_own_pair_removed_on_confirm(tg):
    """Пару опублікували без підтвердження (/repost), партнер без username за неї проголосував, потім
    підтвердився — голос за власну пару має зникнути."""
    async def go():
        settings.tap_guard = 0
        await tg.setup_contest(OWNER)
        p = await tg.submit_pair(EVE, None, "e1", "Пара")
        await tg.send(OWNER, f"/repost {p}")
        await tg.drain()
        await tg.member(NONAME["id"])
        assert "зараховано" in await tg.vote(NONAME, p)
        await tg.send(NONAME, f"/start p{p}")
        await tg.tap(EVE, f"pa:{p}:{NONAME['id']}")
        assert p not in db["votes"][str(NONAME["id"])]["pair"]
    run(go())


def test_guest_added_to_party_chat_without_event_is_rechecked(tg):
    """Бот не адмін чату (подій «вступив» нема). Людину додали в чат — бот перепитає Telegram,
    а не пам'ятатиме «не гість» вічно."""
    from test_party_gate import STRANGER, party_contest

    async def go():
        await party_contest(tg, bot_status="member")
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        assert "лише для гостей" in await tg.vote(STRANGER, n)
        tg.fake.party[STRANGER["id"]] = "member"                 # додали в чат, події бот не бачив
        assert "лише для гостей" in await tg.vote(STRANGER, n)   # кілька секунд пам'ятає відповідь
        ok, at = rt.party_cache[STRANGER["id"]]
        rt.party_cache[STRANGER["id"]] = (ok, at - 60)           # минуло 60 с
        assert "зараховано" in await tg.vote(STRANGER, n)
    run(go())


def test_same_photo_submitted_by_two_people_at_once(tg):
    """Обоє надіслали те саме фото, поки жодне ще не опубліковане. Опублікується лише перше."""
    async def go():
        await tg.setup_contest(OWNER)
        for u, title in ((ALICE, "Перша"), (BOBBY, "Друга")):
            await tg.send(u, "/start")
            await tg.tap(u, "sub")
            await tg.tap(u, "nom:solo")
            await tg.photo(u, "same_photo")
            await tg.send(u, title)
        await tg.tap(ALICE, "pub")
        await tg.tap(BOBBY, "pub")
        await tg.drain()
        assert len(tg.fake.channel_posts()) == 1
        assert "вже подали" in tg.fake.last_to(BOBBY["id"])
    run(go())


def test_audit_burst_threshold_is_inclusive(tg):
    """Рівно BURST_PER_MIN голосів за хвилину — уже сплеск."""
    async def go():
        settings.burst_per_min = 3
        await tg.setup_contest(OWNER)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        for uid in (61, 62, 63):
            await tg.member(uid)
            await tg.vote(TG.user(uid), n)
        await tg.send(OWNER, "/audit")
        assert f"⚠️ №{n}: 3 голосів" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_broadcast_and_remind_skip_banned(tg):
    async def go():
        await tg.setup_contest(OWNER)
        nb = await tg.submit_solo(BOBBY, "b1", "Мумія")
        await tg.send(CAROL, "/start")
        await tg.send(OWNER, f"/ban {nb}")
        before = len(tg.fake.texts_to(BOBBY["id"]))
        await tg.send(OWNER, "/broadcast Всім привіт")
        await tg.send(OWNER, "/remind")
        assert len(tg.fake.texts_to(BOBBY["id"])) == before, "забанений отримав розсилку"
        assert "Всім привіт" in "".join(tg.fake.texts_to(CAROL["id"]))
        assert "Голосування ще триває" in tg.fake.last_to(CAROL["id"])
    run(go())


def test_replace_photo_button_after_submission_closed(tg):
    async def go():
        await tg.setup_contest(OWNER)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.send(OWNER, "/close_submit")
        assert "поки відкритий прийом" in await tg.tap(ALICE, f"rp:{n}")
    run(go())


def test_owner_from_env_is_not_hijacked_by_first_owner_command(tg):
    """OWNER_ID уже в .env (бот на чужому ноуті) — /owner від будь-кого нічого не змінює."""
    async def go():
        settings.owner_id = OWNER["id"]
        db["owner"] = OWNER["id"]
        await tg.send(ALICE, "/owner")
        assert db["owner"] == OWNER["id"] and "вже є власник" in tg.fake.last_to(ALICE["id"])
    run(go())


def test_two_simultaneous_bans_of_same_voter_keep_votes_once(tg):
    """Подвійний тап «🚫» не має задвоїти заморожені голоси (розбан повернув би дублі)."""
    async def go():
        settings.tap_guard = 0
        await tg.setup_contest(OWNER)
        na = await tg.submit_solo(ALICE, "a", "A")
        nb = await tg.submit_solo(BOBBY, "b", "B")
        await tg.member(BOBBY["id"])
        await tg.vote(BOBBY, na)
        tg.fake.delay = 0.02
        await asyncio.gather(tg.tap(OWNER, f"ban:{nb}"), tg.tap(OWNER, f"ban:{nb}"))
        tg.fake.delay = 0
        assert db["banned_votes"][str(BOBBY["id"])] == {"solo": [na]}
        assert db["banned"].count(BOBBY["id"]) == 1
    run(go())

