"""Видалення, бан, відновлення, журнал адмінів, ручне видалення постів, мої костюми."""
from aiogram.methods import DeleteMessage, EditMessageMedia

from conftest import ADMIN2, ALICE, BOBBY, CAROL, DAVE, OWNER, VOTER, run
from halloween_bot.config import settings
from halloween_bot.storage import db


async def contest(tg, voters=(51, 52, 53)):
    await tg.setup_contest(OWNER)
    await tg.send(ADMIN2, "/start")
    await tg.send(OWNER, "/addadmin @adminka")
    await tg.submit_solo(ALICE, "a1", "Костюм Аліси")
    await tg.submit_solo(DAVE, "d1", "Костюм Дейва")
    for uid in voters:
        await tg.member(uid)
        await tg.vote(tg.user(uid), 1)


def test_admin_delete_button_logs_and_alerts_owner(tg):
    async def go():
        await contest(tg)
        await tg.tap(ADMIN2, "del:1")
        e = db["entries"]["1"]
        assert e["status"] == "removed" and sorted(e["removed_votes"]) == [51, 52, 53]
        assert all(1 not in db["votes"][str(u)]["solo"] for u in (51, 52, 53))
        assert any(c.message_id == e["msg_id"] for c in tg.fake.of(DeleteMessage))
        alert = [c for c in tg.fake.calls if getattr(c, "chat_id", None) == OWNER["id"]
                 and "видалив" in (getattr(c, "text", "") or "")][-1]
        assert "@adminka" in alert.text and alert.disable_notification is False, "видалення — зі звуком"
        assert db["admin_log"][-1][2] == "видалив"
    run(go())


def test_ban_blocks_submit_and_vote_restore_unbans(tg):
    async def go():
        await contest(tg)
        await tg.send(ADMIN2, "/ban 1")
        assert ALICE["id"] in db["banned"]
        await tg.member(ALICE["id"])
        assert "не можеш голосувати" in await tg.vote(ALICE, 2)
        await tg.send(ADMIN2, "/restore 1")
        assert ALICE["id"] not in db["banned"]
    run(go())


def test_restore_returns_votes_respecting_limit(tg):
    async def go():
        await contest(tg)
        await tg.send(ADMIN2, "/del 1")
        assert "/restore 1" in tg.fake.last_to(ADMIN2["id"])
        db["votes"]["53"]["solo"] = [101, 102, 103, 104, 105, 106, 107]   # 53-й встиг витратити всі голоси
        await tg.send(ADMIN2, "/restore 1")
        reply = tg.fake.last_to(ADMIN2["id"])
        assert "Голосів відновлено: 2" in reply and "Не вдалося повернути 1" in reply
        e = db["entries"]["1"]
        assert e["status"] == "queued" and "removed_votes" not in e
        await tg.drain()
        assert e["status"] == "published"
        assert "повернули в конкурс" in tg.fake.last_to(ALICE["id"])
        assert db["admin_log"][-1][2] == "відновив"
    run(go())


def test_restore_refusals(tg):
    async def go():
        await contest(tg)
        await tg.send(OWNER, "/restore 2")
        assert "не видалений" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/restore abc")
        assert "Використання" in tg.fake.last_to(OWNER["id"])
        # автор зняв сам → адмін не повертає
        await tg.tap(DAVE, "wd:2")
        await tg.tap(DAVE, "wdy:2")
        await tg.send(OWNER, "/restore 2")
        assert "автор зняв сам" in tg.fake.last_to(OWNER["id"])
        # у автора вже новий костюм → конфлікт
        await tg.send(OWNER, "/del 1")
        await tg.submit_solo(ALICE, "a2", "Новий костюм Аліси")
        await tg.send(OWNER, "/restore 1")
        assert "інший костюм №3" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_owner_actions_not_echoed_to_owner_and_adminlog_is_owner_only(tg):
    async def go():
        await contest(tg)
        n = len(tg.fake.texts_to(OWNER["id"]))
        await tg.send(OWNER, "/del 2")
        assert not any("📝" in t for t in tg.fake.texts_to(OWNER["id"])[n:])
        await tg.send(OWNER, "/adminlog")
        log = tg.fake.last_to(OWNER["id"])
        assert "Журнал дій адмінів" in log and "kyrylo: видалив" in log
        await tg.send(ADMIN2, "/adminlog")
        assert "Тисни /start" in tg.fake.last_to(ADMIN2["id"]), "не-власник не бачить журнал"
    run(go())


def test_manual_channel_deletion_detected(tg):
    async def go():
        await contest(tg)
        tg.fake.deleted.add(db["entries"]["1"]["msg_id"])
        await tg.send(OWNER, "/results 2")
        res = tg.fake.last_to(OWNER["id"])
        assert "1. №2" in res and "видалені з каналу вручну" in res and "№1" in res.split("🗑")[1]
        assert db["entries"]["1"]["removed_by"] == "system"
        tg.fake.deleted.add(db["entries"]["2"]["msg_id"])
        await tg.send(OWNER, "/verify")
        assert "№2" in tg.fake.last_to(OWNER["id"])
        await tg.send(OWNER, "/restore 1")
        assert "повертається" in tg.fake.last_to(OWNER["id"]), "знятий системою — можна повернути"
    run(go())


def test_quiet_admin_gets_only_suspicious(tg):
    async def go():
        await contest(tg)
        await tg.send(ADMIN2, "/quiet")
        n = len(tg.fake.texts_to(ADMIN2["id"]))
        await tg.submit_solo(CAROL, "c1", "Звичайний")
        assert len(tg.fake.texts_to(ADMIN2["id"])) == n
        await tg.send(ADMIN2, "/quiet")
        assert "отримуєш копію" in tg.fake.last_to(ADMIN2["id"])
        n = len(tg.fake.texts_to(ADMIN2["id"]))
        await tg.submit_solo(BOBBY, "b1", "Ще один")
        assert len(tg.fake.texts_to(ADMIN2["id"])) == n + 1 and "Ще один" in tg.fake.last_to(ADMIN2["id"])
    run(go())


def test_repost_failed_entry(tg):
    async def go():
        await tg.setup_contest(OWNER)

        def fail(method):
            if method.chat_id < 0:
                raise ConnectionError("hotspot down")
        tg.fake.hooks["SendPhoto"] = fail
        await tg.submit_solo(ALICE, "a1", "Невдаха")
        assert db["entries"]["1"]["status"] == "failed"
        assert "Не вдалося опублікувати №1" in tg.fake.last_to(OWNER["id"])
        del tg.fake.hooks["SendPhoto"]
        await tg.send(OWNER, "/repost 1")
        await tg.drain()
        assert db["entries"]["1"]["status"] == "published"
    run(go())


def test_my_entries_withdraw_and_replace_photo(tg):
    async def go():
        await contest(tg)
        await tg.tap(ALICE, "my")
        assert "Пост у каналі" in tg.fake.last_to(ALICE["id"])
        await tg.tap(ALICE, "rp:1")
        await tg.photo(ALICE, "a1_new")
        e = db["entries"]["1"]
        assert e["photo"] == "a1_new" and e["replaced"] == 1
        assert tg.fake.of(EditMessageMedia)[-1].media.media == "a1_new"
        assert sorted(int(u) for u, v in db["votes"].items() if 1 in v.get("solo", [])) == [51, 52, 53]
        await tg.tap(DAVE, "rp:1")
        assert "Надішли нове фото для №1" not in tg.fake.last_to(DAVE["id"]), "чужу заявку не замінити"
        settings.max_replaces = 1
        assert "Ліміт замін" in await tg.tap(ALICE, "rp:1")
        await tg.tap(ALICE, "wd:1")
        await tg.tap(ALICE, "wdn:1")
        assert db["entries"]["1"]["status"] == "published"
        await tg.tap(ALICE, "wd:1")
        await tg.tap(ALICE, "wdy:1")
        assert db["entries"]["1"]["status"] == "removed"
        assert await tg.submit_solo(ALICE, "a3", "Після зняття") == 3
    run(go())


def test_partner_sees_pair_read_only(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(BOBBY, "/start")
        await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Пара")
        await tg.tap(BOBBY, "my")
        assert "Ти тут партнер" in tg.fake.last_to(BOBBY["id"])
    run(go())


def test_my_votes(tg):
    async def go():
        await contest(tg)
        await tg.tap(tg.user(51), "myv")
        assert "1/7" in tg.fake.last_to(51) and "Костюм Аліси" in tg.fake.last_to(51)
        await tg.send(VOTER, "/myvotes")
        assert "ще не голосував" in tg.fake.last_to(VOTER["id"])
    run(go())


def test_double_remove_does_not_lose_votes_for_restore(tg):
    """Адмін тисне 🗑 під копією і водночас інший пише /del: видалення пауза (запит у Telegram).
    Друге видалення не має затерти список голосів, які /restore поверне."""
    import asyncio

    async def go():
        await contest(tg)
        assert db["entries"]["1"]["status"] == "published"
        voters_before = sorted(int(u) for u, v in db["votes"].items() if 1 in v.get("solo", []))
        assert voters_before
        tg.fake.delay = 0.02
        await asyncio.gather(tg.tap(ADMIN2, "del:1"), tg.send(OWNER, "/del 1"))
        tg.fake.delay = 0
        assert sorted(db["entries"]["1"]["removed_votes"]) == voters_before
        await tg.send(OWNER, "/restore 1")
        assert f"Голосів відновлено: {len(voters_before)}" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_old_post_that_cannot_be_deleted_is_marked_removed(tg):
    """Пост старший за 48 год: Telegram не дає боту його видалити. Тоді прибираємо кнопку й пишемо «знято»."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageCaption

    def too_old(m):
        raise TelegramBadRequest(method=m, message="Bad Request: message can't be deleted")

    async def go():
        await contest(tg)
        tg.fake.hooks["DeleteMessage"] = too_old
        await tg.send(OWNER, "/del 1")
        assert db["entries"]["1"]["status"] == "removed"
        edits = [c for c in tg.fake.of(EditMessageCaption) if c.message_id == db["entries"]["1"]["msg_id"]]
        assert edits and "знято з конкурсу" in edits[-1].caption and edits[-1].reply_markup is None
    run(go())
