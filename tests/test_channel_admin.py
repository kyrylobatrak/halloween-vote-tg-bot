"""Канал, власник, адміни, відкриття/закриття, звіти, розсилки."""
import io
import time
import zipfile

from aiogram.methods import CreateChatInviteLink, ExportChatInviteLink, SendDocument

from conftest import ADMIN2, ALICE, BOBBY, CHANNEL, DAVE, OWNER, run
from halloween_bot.config import settings
from halloween_bot.storage import db, store


# ───────── власник і адміни ─────────
def test_owner_claim_persists_to_env_and_cannot_be_hijacked(tg):
    async def go():
        await tg.send(OWNER, "/start")
        assert "/owner" in tg.fake.last_to(OWNER["id"])          # підказка стати власником (текст може бути будь-який)
        await tg.send(OWNER, "/owner")
        assert "власник бота" in tg.fake.last_to(OWNER["id"])
        assert f"OWNER_ID={OWNER['id']}" in settings.env_file.read_text()
        await tg.send(ALICE, "/owner")
        assert "У бота вже є" in tg.fake.last_to(ALICE["id"]) and db["owner"] == OWNER["id"]
        # data.json зник — власник однаково з .env
        store.reset()
        store.load()
        assert db["owner"] == OWNER["id"]
    run(go())


def test_addadmin_requires_start_and_admin_filter(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ADMIN2, "/stats")
        assert "Тисни /start" in tg.fake.last_to(ADMIN2["id"]), "не-адмін не бачить адмін-команд"
        await tg.send(OWNER, "/addadmin @adminka")
        assert "Не знаю" in tg.fake.last_to(OWNER["id"])
        await tg.send(ADMIN2, "/start")
        await tg.send(OWNER, "/addadmin @adminka")
        assert "тепер адмін" in tg.fake.last_to(OWNER["id"])
        assert "зробили адміном" in tg.fake.last_to(ADMIN2["id"])
        await tg.send(ADMIN2, "/stats")
        assert "Статистика" in tg.fake.last_to(ADMIN2["id"])
        await tg.send(ADMIN2, "/addadmin @alice_w")
        assert "Тисни /start" in tg.fake.last_to(ADMIN2["id"]), "додавати адмінів може лише власник"
        await tg.send(OWNER, "/admins")
        assert "👑 @kyrylo" in tg.fake.last_to(OWNER["id"]) and "🛡 @adminka" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/deladmin @adminka")
        await tg.send(ADMIN2, "/stats")
        assert "Тисни /start" in tg.fake.last_to(ADMIN2["id"])
    run(go())


def test_toggles_announce_in_channel(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(OWNER, "/close_vote")
        assert db["vote_open"] is False
        assert "Голосування завершено" in tg.fake.last_to(CHANNEL)
        await tg.send(OWNER, "/open_vote")
        assert "до 7 голосів" in tg.fake.last_to(CHANNEL)
    run(go())


# ───────── канал ─────────
def test_channel_connect_uses_non_revoking_link(tg):
    async def go():
        await tg.send(OWNER, "/owner")
        await tg.bot_status(OWNER, "administrator")
        assert db["channel_id"] == CHANNEL and db["channel_link"] == "https://t.me/+LINK1"
        assert not tg.fake.of(ExportChatInviteLink), "exportChatInviteLink анулював би старі посилання"
        await tg.bot_status(OWNER, "administrator", can_delete_messages=False)
        assert db["channel_link"] == "https://t.me/+LINK1", "зміна прав не міняє посилання"
        assert len(tg.fake.of(CreateChatInviteLink)) == 1
        assert "бракує прав: видаляти" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/newlink")
        assert db["channel_link"] == "https://t.me/+LINK2"
        await tg.bot_status(OWNER, "left")
        assert db["channel_id"] is None and "Публікація на паузі" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_channel_added_by_stranger_is_ignored(tg):
    async def go():
        await tg.send(OWNER, "/owner")
        await tg.bot_status(ALICE, "administrator")
        assert db["channel_id"] is None
        await tg.send(ALICE, "/owner")                 # власник уже є: Аліса канал не підключить
        assert db["channel_id"] is None
    run(go())


def test_channel_added_before_owner_connects_after_owner(tg):
    """Типова помилка на старті: спершу додали бота в канал, а /owner написали потім."""
    async def go():
        await tg.send(OWNER, "/start")
        await tg.bot_status(OWNER, "administrator")    # ще не власник — подію відкладено
        assert db["channel_id"] is None and db["pending_channel"]["by"] == OWNER["id"]
        await tg.send(OWNER, "/owner")
        assert db["channel_id"] == CHANNEL and db["channel_link"]
        assert db["pending_channel"] is None
        assert any("підключено" in t for t in tg.fake.texts_to(OWNER["id"]))
    run(go())


def test_posts_wait_until_channel_connected(tg):
    async def go():
        await tg.send(OWNER, "/owner")
        db["submit_open"] = True
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.photo(ALICE, "a1")
        await tg.send(ALICE, "Чекаю канал")
        await tg.tap(ALICE, "pub")
        assert db["entries"]["1"]["status"] == "queued"
        await tg.tap(ALICE, "my")
        assert "у черзі на публікацію: 1-й" in tg.fake.last_to(ALICE["id"])
        await tg.bot_status(OWNER, "administrator")
        await tg.drain()
        assert db["entries"]["1"]["status"] == "published"
    run(go())


# ───────── звіти ─────────
async def votes_setup(tg):
    await tg.setup_contest(OWNER)
    for i, u in enumerate([ALICE, BOBBY, DAVE]):
        await tg.submit_solo(u, f"p{i}", f"Костюм {i}")
    for uid, nums in {51: [1, 2], 52: [1], 53: [2], 54: [3]}.items():
        await tg.member(uid)
        for n in nums:
            await tg.vote(tg.user(uid), n)


def test_results_and_tie(tg):
    async def go():
        await votes_setup(tg)
        await tg.send(OWNER, "/results 3")
        r = tg.fake.last_to(OWNER["id"])
        assert "1. №1" in r and "2. №2" in r and "Нічия за 1 місце" in r
        assert "заявок нема" in r
    run(go())


def test_stats(tg):
    async def go():
        await votes_setup(tg)
        await tg.send(OWNER, "/stats")
        s = tg.fake.last_to(OWNER["id"])
        assert "3 опубл." in s and "Проголосувало людей: 4, усього голосів: 5" in s
    run(go())


def test_audit_burst_and_details_and_votelog(tg):
    async def go():
        await votes_setup(tg)
        now = int(time.time())
        for i in range(10):
            store.vlog.append([now + i, 900 + i, "solo", 3, 1])
        await tg.send(OWNER, "/audit")
        assert "№3: 11 голосів за хвилину" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/audit 1")
        a = tg.fake.last_to(OWNER["id"])
        assert "Голосів зараз: <b>2</b>" in a and "@user51x" in a and "@user52x" in a
        await tg.send(OWNER, "/audit 999")
        assert "Використання" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/votelog")
        doc = tg.fake.of(SendDocument)[-1]
        data = doc.document.data.decode("utf-8")
        assert data.startswith("﻿") and "@user51x" in data and doc.document.filename.endswith(".csv")
    run(go())


def test_health(tg):
    async def go():
        await votes_setup(tg)
        tg.fake.bot_rights["can_delete_messages"] = False
        await tg.send(OWNER, "/health")
        h = tg.fake.last_to(OWNER["id"])
        for part in ("Стан бота", "Telegram: ✅", "нема прав: видаляти", "Черга постів: 0",
                     "журнал голосів: 5", "Подача: 🟢"):
            assert part in h, part
    run(go())


def test_backup_command_sends_zip(tg):
    async def go():
        await votes_setup(tg)
        await tg.send(OWNER, "/backup")
        doc = tg.fake.of(SendDocument)[-1]
        z = zipfile.ZipFile(io.BytesIO(doc.document.data))
        assert sorted(z.namelist()) == ["data.json", "votes.log"]
        assert len(z.read("votes.log").decode().splitlines()) == 5
    run(go())


# ───────── розсилки ─────────
def test_broadcast_parallel_and_survives_network_errors(tg):
    async def go():
        await tg.setup_contest(OWNER)
        db["users"].extend(range(1000, 1060))
        settings.send_rate = 25
        tg.fake.delay = 0.15                             # хотспот: 150 мс на запит

        def flaky(method):
            if isinstance(method.chat_id, int) and 1000 <= method.chat_id and method.chat_id % 3 == 0:
                raise ConnectionError("hotspot")
        tg.fake.hooks["SendMessage"] = flaky
        t0 = time.perf_counter()
        await tg.send(OWNER, "/broadcast Лишилось 10 хв!")
        dt = time.perf_counter() - t0
        assert dt < 4.5, f"паралельно ~2.5 с, а не {dt:.1f} (послідовно було б ~12 с)"
        assert "Надіслано: 41/61" in tg.fake.last_to(OWNER["id"]) and "не доставлено: 20" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_remind_personal_counts(tg):
    async def go():
        await votes_setup(tg)
        db["users"].extend([51, 52])
        db["votes"]["52"] = {"solo": list(range(1, 8)), "pair": list(range(1, 8))}
        await tg.send(OWNER, "/remind Закінчуємо о 23:00")
        r51 = tg.fake.last_to(51)
        assert "Закінчуємо о 23:00" in r51 and "<b>5</b> з 7" in r51 and "<b>7</b> з 7" in r51
        assert not any("Голосування ще триває" in t for t in tg.fake.texts_to(52))
        assert "Усі голоси вже використали: 1" in tg.fake.last_to(OWNER["id"])
        db["vote_open"] = False
        await tg.send(OWNER, "/remind")
        assert "нагадувати нема про що" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_command_menu_for_guests_admins_and_owner(tg):
    from aiogram.methods import DeleteMyCommands, SetMyCommands
    from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

    def menu_for(scope_cls, chat_id=None):
        calls = [c for c in tg.fake.of(SetMyCommands) if isinstance(c.scope, scope_cls)
                 and (chat_id is None or c.scope.chat_id == chat_id)]
        return {x.command for x in calls[-1].commands} if calls else set()

    async def go():
        await tg.send(OWNER, "/owner")
        await tg.send(ALICE, "/start")
        await tg.send(OWNER, "/addadmin @alice_w")
        guest, owner, admin = (menu_for(BotCommandScopeDefault), menu_for(BotCommandScopeChat, OWNER["id"]),
                               menu_for(BotCommandScopeChat, ALICE["id"]))
        assert {"start", "my", "myvotes"} <= guest and "stats" not in guest
        assert {"stats", "open_vote", "close_vote"} <= admin and "backup" not in admin
        assert {"backup", "admins", "stats"} <= owner
        await tg.send(OWNER, "/deladmin @alice_w")
        dels = tg.fake.of(DeleteMyCommands)
        assert dels and dels[-1].scope.chat_id == ALICE["id"]
    run(go())


def test_invite_link_retried_when_telegram_not_ready(tg, monkeypatch):
    """Справжній випадок з першого запуску: «bot is not a member of the channel chat» одразу
    після того, як бота зробили адміном. Бот має перечекати й таки створити посилання."""
    from aiogram.exceptions import TelegramForbiddenError
    from halloween_bot.services import channel as channel_service
    monkeypatch.setattr(channel_service, "INVITE_RETRY_DELAY", 0)
    fails = {"left": 2}

    def not_yet(m):
        if fails["left"]:
            fails["left"] -= 1
            raise TelegramForbiddenError(method=m, message="Forbidden: bot is not a member of the channel chat")
    tg.fake.hooks["CreateChatInviteLink"] = not_yet

    async def go():
        await tg.send(OWNER, "/start")
        await tg.send(OWNER, "/owner")
        await tg.bot_status(OWNER, "administrator")
        assert db["channel_link"] and db["channel_link_mode"] == "open"
        assert "Не вдалося створити посилання" not in tg.fake.last_to(OWNER["id"])
    run(go())
