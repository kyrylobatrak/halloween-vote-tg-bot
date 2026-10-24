"""Крайні випадки: застарілі кнопки після перезапуску, паралельні натискання, «чужі» чати."""
import asyncio

from aiogram.methods import AnswerCallbackQuery

from conftest import ALICE, BOBBY, CAROL, OWNER, run
from halloween_bot import app
from halloween_bot.storage import db


def unanswered(tg) -> list[str]:
    """Callback-и, на які бот не відповів (у людини вічно крутиться «годинник»)."""
    answered = {c.callback_query_id for c in tg.fake.of(AnswerCallbackQuery)}
    return [f"cb{i}" for i in range(1, tg._cb + 1) if f"cb{i}" not in answered]


def test_stale_buttons_after_restart_are_answered(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.photo(ALICE, "a1")
        await tg.send(ALICE, "Назва")
        app.create_dispatcher().storage.storage.clear()       # перезапуск бота: FSM у пам'яті зник
        for data in ("pub", "nouser", "cancel", "zzz:1"):
            await tg.tap(ALICE, data)
        assert not unanswered(tg), unanswered(tg)
        assert "застаріла" in (await tg.tap(ALICE, "pub") or "")
    run(go())


def test_double_tap_on_publish_creates_one_entry(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.photo(ALICE, "a1")
        await tg.send(ALICE, "Назва")
        await asyncio.gather(tg.tap(ALICE, "pub"), tg.tap(ALICE, "pub"), tg.tap(ALICE, "pub"))
        await tg.drain()
        assert db["seq"] == 1 and len(tg.fake.channel_posts()) == 1
    run(go())


def test_concurrent_pairs_with_same_partner(tg):
    async def go():
        await tg.setup_contest(OWNER)
        for u in (ALICE, BOBBY):
            await tg.send(u, "/start")
            await tg.tap(u, "sub")
            await tg.tap(u, "nom:pair")
            await tg.send(u, "@carol_m")
            await tg.photo(u, f"p{u['id']}")
            await tg.send(u, "Пара з Керол")
        await asyncio.gather(tg.tap(ALICE, "pub"), tg.tap(BOBBY, "pub"))
        await tg.drain()
        assert db["seq"] == 2          # вписати Керол можуть обоє: поки вона не підтвердила, це лише запрошення
        await tg.send(CAROL, "/start")
        await asyncio.gather(tg.tap(CAROL, "pc:1"), tg.tap(CAROL, "pc:2"))     # одночасно «Так, це я» в обох
        await tg.drain()
        confirmed = [e for e in db["entries"].values() if e.get("partner_id") == CAROL["id"]]
        assert len(confirmed) == 1, "одна людина = одна пара навіть при одночасному підтвердженні"
        assert sum(1 for e in db["entries"].values() if e["status"] == "published") == 1
    run(go())


def test_group_messages_ignored(tg):
    async def go():
        await tg.setup_contest(OWNER)
        n = len(tg.fake.calls)
        await tg._feed({"message": {"message_id": 1, "date": 0, "text": "/start",
                                    "chat": {"id": -555, "type": "group", "title": "Тусовка"},
                                    "from": ALICE}})
        assert len(tg.fake.calls) == n, "бот мовчить у групах (не спамить загальний чат)"
    run(go())


CALLBACKS = ["sub", "nom:solo", "nom:pair", "nom:bad", "nouser", "pub", "cancel", "my", "myv",
             "v:1", "v:999", "pc:1", "pc:999", "pd:1", "pd:999", "wd:1", "wd:999", "wdy:1", "wdn:1",
             "rp:1", "rp:999", "del:1", "ban:999", "pa:1:77", "pr:1:77", "pa:999:1", "zzz"]


def test_every_button_always_answered_never_crashes(tg):
    """Кожна кнопка від різних людей у «неправильних» ситуаціях: бот відповідає й не падає."""
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Пара")
        for user in (BOBBY, tg.user(77), ALICE):            # партнер, незнайомець, автор
            for data in CALLBACKS:
                await tg.tap(user, data)
                await tg.tap(user, data, in_channel=True)
        db["entries"]["1"]["status"] = "removed"
        db["vote_open"] = db["submit_open"] = False
        for data in CALLBACKS:
            await tg.tap(tg.user(78), data)
        assert not unanswered(tg), unanswered(tg)
        assert db["entries"]["1"]["status"] == "removed", "чужі кнопки нічого не зламали"
    run(go())


def test_crash_in_handler_is_reported_not_silent(tg, monkeypatch):
    """Якщо обробник впав: кнопка не «крутиться» вічно, людина отримує відповідь, /health показує збій."""
    from halloween_bot.handlers import voting as voting_handlers
    from halloween_bot.runtime import rt
    from halloween_bot.services import reports

    def boom(*a, **kw):
        raise RuntimeError("штучна поломка")

    async def go():
        await tg.setup_contest(OWNER)
        num = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.member(BOBBY["id"])
        monkeypatch.setattr(voting_handlers, "cast_vote", boom)
        answer = await tg.vote(BOBBY, num)
        assert answer and "Щось пішло не так" in answer
        monkeypatch.setattr(reports, "stats_text", boom)
        await tg.send(OWNER, "/stats")
        assert "Щось пішло не так" in tg.fake.last_to(OWNER["id"])
        assert len(rt.errors) == 2
        await tg.send(OWNER, "/health")
        assert "Помилок в обробниках: 2" in tg.fake.last_to(OWNER["id"])
        assert "штучна поломка" in tg.fake.last_to(OWNER["id"])
        rt.errors.clear()          # помилки тут навмисні — не валимо тест у teardown
    run(go())


def test_split_text_respects_limit_and_keeps_everything():
    from halloween_bot.services.messaging import split_text
    assert split_text("") == [""]
    assert split_text("коротко") == ["коротко"]
    text = "\n".join(f"{i}. " + "Я" * (i % 90) for i in range(400))
    parts = split_text(text, 500)
    assert all(0 < len(p) <= 500 for p in parts)
    assert "\n".join(parts) == text                      # нічого не загубили й не переставили
    long_line = "x" * 1234
    assert [len(p) for p in split_text(long_line, 500)] == [500, 500, 234]


def test_long_results_are_split_into_several_messages(tg):
    """30 місць з максимально довгими іменами — це >4096 символів; Telegram такого не прийме."""
    from conftest import TG

    async def go():
        from halloween_bot.config import settings
        settings.channel_per_min = 10**6
        await tg.setup_contest(OWNER)
        for i in range(32):
            u = TG.user(500 + i, None)                     # без @username — у звіті повне ім'я
            u["first_name"], u["last_name"] = "Я" * 64, "<&>" * 21
            await tg.submit_solo(u, f"long{i}", "Назва" * 10)
        before = len(tg.fake.texts_to(OWNER["id"]))
        await tg.send(OWNER, "/results 30")
        sent = tg.fake.texts_to(OWNER["id"])[before:]
        assert len(sent) >= 2 and all(len(t) <= 4096 for t in sent)
    run(go())


def test_stale_callback_after_offline_is_not_a_bug(tg):
    """Інтернет пропав → натискання накопичились → бот обробляє їх із запізненням.
    Голос зараховується, а «query is too old» на відповідь не рахується як помилка."""
    from aiogram.exceptions import TelegramBadRequest
    from conftest import BOBBY
    from halloween_bot.runtime import rt

    def too_old(m):
        raise TelegramBadRequest(method=m, message="Bad Request: query is too old and response timeout expired")

    async def go():
        await tg.setup_contest(OWNER)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.member(BOBBY["id"])
        tg.fake.hooks["AnswerCallbackQuery"] = too_old
        await tg.vote(BOBBY, n)
        assert db["votes"][str(BOBBY["id"])]["solo"] == [n]
        assert not rt.errors
    run(go())


def test_buttons_under_messages_older_than_48h(tg):
    """Подачу відкрили за кілька днів до туси: партнер підтверджує пару, адмін тисне 🗑 під старою копією,
    автор знімає костюм — під повідомленнями, яким понад 48 год. Telegram дає їх як «недоступні»."""
    from conftest import CAROL
    from halloween_bot.runtime import rt

    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(CAROL, "/start")
        p = await tg.submit_pair(ALICE, "@carol_m", "o1", "Стара пара")
        await tg.tap(CAROL, f"pc:{p}", old=True)
        await tg.drain()
        assert db["entries"][str(p)]["partner_id"] == CAROL["id"]
        assert "Готово" in "".join(tg.fake.texts_to(CAROL["id"]))
        n = await tg.submit_solo(BOBBY, "o2", "Старий соло")
        assert "Видалено" in await tg.tap(OWNER, f"del:{n}", old=True)
        await tg.send(OWNER, f"/restore {n}")
        await tg.drain()
        await tg.tap(BOBBY, f"wd:{n}", old=True)
        await tg.tap(BOBBY, f"wdy:{n}", old=True)
        assert db["entries"][str(n)]["status"] == "removed"
        assert not rt.errors
    run(go())
