"""Сховище, бекапи, конфіг, системні речі (блокування, токен, версія Python), черга постів."""
import asyncio
import builtins
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import ALICE, OWNER, ROOT, TEST_HOME, run
from halloween_bot import config
from halloween_bot.config import settings
from halloween_bot.runtime import rt
from halloween_bot.services import backups, messaging, publisher, system
from halloween_bot.storage import StorageError, db, store


# ───────── відкладений запис ─────────
def test_many_changes_one_write():
    async def go():
        for i in range(500):
            db["users"].append(i)
            store.save()
        assert store.version == 0 and not settings.data_path.exists()
        assert await store.flush()
        assert store.version == 1 and len(json.loads(settings.data_path.read_text())["users"]) == 500
        assert await store.flush() and store.version == 1, "без змін — без запису"
    run(go())


def test_permission_error_retried(monkeypatch):
    real = os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 4:
            raise PermissionError(13, "антивірус тримає файл")
        return real(a, b)
    settings.io_retry_delay = 0.001
    monkeypatch.setattr(os, "replace", flaky)
    db["seq"] = 42
    store.save()
    assert run(store.flush())
    assert calls["n"] == 4 and json.loads(settings.data_path.read_text())["seq"] == 42


def test_long_lock_keeps_data_and_alerts_owner_once(tg, monkeypatch):
    async def go():
        await tg.send(OWNER, "/owner")
        settings.io_retries, settings.io_retry_delay = 2, 0.001
        real = os.replace

        def dead(a, b):
            raise PermissionError(13, "locked")
        monkeypatch.setattr(os, "replace", dead)
        db["seq"] = 7
        store.save()
        assert not await backups.flush_and_alert(tg.bot)
        assert not await backups.flush_and_alert(tg.bot)
        sos = [t for t in tg.fake.texts_to(OWNER["id"]) if "🆘" in t]
        assert len(sos) == 1 and store.dirty
        monkeypatch.setattr(os, "replace", real)
        assert await backups.flush_and_alert(tg.bot)
        assert "знову працює" in tg.fake.last_to(OWNER["id"])
        assert json.loads(settings.data_path.read_text())["seq"] == 7
    run(go())


def test_votes_log_append_retry_without_duplicates(monkeypatch):
    async def go():
        settings.io_retries, settings.io_retry_delay = 2, 0.001
        store.log_vote(1, "solo", 1, 1)
        assert await store.flush()
        store.log_vote(2, "solo", 1, 1)
        real_open = builtins.open
        fails = {"n": 0}

        def flaky_open(path, mode="r", *a, **k):
            if str(path).endswith("votes.log") and "a" in mode and fails["n"] < 2:
                fails["n"] += 1
                raise PermissionError(13, "locked")
            return real_open(path, mode, *a, **k)
        monkeypatch.setattr(builtins, "open", flaky_open)
        assert not await store.flush() and len(store.pending) == 1
        monkeypatch.setattr(builtins, "open", real_open)
        assert await store.flush()
        assert len(settings.votes_log_path.read_text().splitlines()) == 2
    run(go())


def test_old_format_votes_log_migrated():
    settings.data_path.write_text(json.dumps({"votes_log": [[1, 10, "solo", 1, 1], [2, 11, "solo", 1, 1]]}))
    store.load()
    assert len(store.vlog) == 2 and "votes_log" not in db
    assert len(settings.votes_log_path.read_text().splitlines()) == 2


def test_truncated_last_line_of_votes_log_ignored():
    settings.votes_log_path.write_text('[1,10,"solo",1,1]\n[2,11,"so')
    store.load()
    assert len(store.vlog) == 1


def test_corrupted_data_json_recovered_from_backup():
    settings.backups_dir.mkdir()
    (settings.backups_dir / "data_1031_200000.json").write_text(json.dumps({"seq": 5}))
    (settings.backups_dir / "data_1031_210000.json").write_text(json.dumps({"seq": 9}))
    settings.data_path.write_text('{"seq": 12, "entr')          # обірваний запис
    store.load()
    assert db["seq"] == 9 and store.recovered_from.name == "data_1031_210000.json"
    assert list(TEST_HOME.glob("data.broken_*.json")), "пошкоджений файл збережено, не видалено"
    assert store.dirty, "відновлена версія одразу запишеться"


def test_corrupted_without_backup_stops_with_clear_message():
    settings.data_path.write_text("{oops")
    with pytest.raises(StorageError, match="zip-бекап"):
        store.load()


def test_owner_from_env_overrides_data():
    settings.data_path.write_text(json.dumps({"owner": 777}))
    settings.owner_id = 555
    store.load()
    assert db["owner"] == 555


def test_local_backup_rotation_and_zip():
    async def go():
        settings.local_backups_keep = 3
        settings.backups_dir.mkdir()
        for i in range(6):
            (settings.backups_dir / f"data_0101_00000{i}.json").write_text("{}")
        store.log_vote(1, "solo", 1, 1)
        await store.flush()
        store.local_backup()
        assert len(list(settings.backups_dir.glob("data_*.json"))) == 3
        assert (settings.backups_dir / "votes.log").exists()
    run(go())


def test_backup_tick_respects_intervals(tg):
    async def go():
        await tg.send(OWNER, "/owner")
        await store.flush()
        settings.local_backup_every = 0
        settings.vote_backup_every = settings.backup_every = 0
        state = {"local_version": -1, "tg_version": -1, "last_local": 0, "last_tg": 0}
        await backups.backup_tick(tg.bot, state)
        assert list(settings.backups_dir.glob("data_*.json")) and rt.last_tg_backup
        n_docs = len([c for c in tg.fake.calls if type(c).__name__ == "SendDocument"])
        await backups.backup_tick(tg.bot, state)
        assert len([c for c in tg.fake.calls if type(c).__name__ == "SendDocument"]) == n_docs, \
            "без змін — без нового бекапу"
    run(go())


def test_flush_sync_on_exit():
    store.log_vote(1, "solo", 1, 1)
    db["seq"] = 3
    store.save()
    store.flush_sync()
    assert json.loads(settings.data_path.read_text())["seq"] == 3
    assert settings.votes_log_path.read_text().strip()


# ───────── конфіг ─────────
def test_config_strips_inline_comments_and_resolves_paths(monkeypatch):
    monkeypatch.setenv("MAX_VOTES", "5   # голосів на номінацію")
    monkeypatch.setenv("REQUIRE_MEMBER", "0  # будь-хто")
    monkeypatch.setenv("DATA_PATH", "sub/data.json")
    monkeypatch.setenv("TAP_GUARD_SECONDS", "не число")
    s = config.load_settings()
    assert s.max_votes == 5 and s.require_member is False and s.tap_guard == 5
    assert s.data_path == TEST_HOME / "sub" / "data.json"


# ───────── система ─────────
def test_single_instance_lock_between_processes():
    code = ("import sys,time; sys.path.insert(0,{root!r});"
            "from halloween_bot.services.system import acquire_lock, AlreadyRunning;"
            "\ntry:\n  l=acquire_lock(); print('LOCKED',flush=True); time.sleep(2)"
            "\nexcept AlreadyRunning:\n  print('BUSY',flush=True)").format(root=str(ROOT))
    env = dict(os.environ)
    first = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    assert first.stdout.readline().strip() == "LOCKED"
    second = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    first.wait()
    assert second.stdout.strip() == "BUSY"


def test_ensure_token_asks_until_valid_and_saves():
    settings.bot_token = ""
    answers = iter(["не токен", "123:short", "987654:" + "B" * 35])
    said = []
    tok = system.ensure_token(ask=lambda _: next(answers), say=said.append)
    assert tok.startswith("987654:") and settings.bot_token == tok
    assert sum("Не схоже" in s for s in said) == 2
    assert f"BOT_TOKEN={tok}" in settings.env_file.read_text()


def test_keep_awake_is_noop_off_windows():
    assert system.keep_awake() is (False if os.name != "nt" else True)


def test_old_python_gets_clear_message():
    code = ("import sys; sys.version_info=(3,9,18); sys.version='3.9.18 (fake)';"
            "src=open({p!r},encoding='utf-8').read();"
            "exec(compile(src,'bot.py','exec'),{{'__name__':'__main__'}})").format(p=str(ROOT / "bot.py"))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}     # Windows інакше читає вивід у cp1251
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, input="",
                       encoding="utf-8", env=env)
    assert r.returncode == 1 and "Потрібен Python 3.10" in r.stdout and "3.9.18" in r.stdout


def test_app_run_refuses_second_instance(capsys, monkeypatch):
    lock = system.acquire_lock()
    monkeypatch.setattr(builtins, "input", lambda *_: "")
    try:
        # другий бот — окремий процес (як другий start.bat), блокування між процесами працює і на Windows
        code = ("import sys; sys.path.insert(0,{root!r}); from halloween_bot.app import run; "
                "sys.exit(run())").format(root=str(ROOT))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, input="\n",
                           encoding="utf-8", env=env)
        assert r.returncode == 1 and "Бот уже запущено" in r.stdout
    finally:
        lock.close()


def test_startup_report(tg, capsys):
    async def go():
        await tg.send(OWNER, "/owner")
        await tg.bot_status(OWNER, "administrator")
        from halloween_bot import app
        tg.fake.hooks["GetChat"] = lambda m: type("C", (), {"title": "Costumes"})()
        await app.startup_report(tg.bot)
    run(go())
    out = capsys.readouterr().out
    assert "@hw_test_bot запущено" in out and "Канал: Costumes" in out


# ───────── черга публікацій ─────────
def test_channel_rate_limit():
    async def go():
        settings.channel_per_min, settings.post_interval = 5, 0.001
        now = time.time()
        for i in range(5):
            rt.channel_sends.append(now - 59.5 + i * 0.01)
        t0 = time.perf_counter()
        await messaging.channel_slot()
        return time.perf_counter() - t0
    dt = run(go())
    assert 0.4 < dt < 1.2


def test_queue_position_eta():
    for n in range(10, 40):
        db["entries"][str(n)] = {"num": n, "status": "queued"}
    settings.post_interval = 3.5
    assert publisher.queue_position(39) == (30, 105)        # 30 × 3.5 с (суворіше, ніж 19/хв)
    settings.post_interval = 0.5
    assert publisher.queue_position(39) == (30, 94)         # тепер обмежує 19 постів/хв
    assert publisher.queue_position(10)[0] == 1


def test_publisher_loop_survives_broken_entry(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_solo(ALICE, "a1", "Нормальна")
        good = dict(db["entries"]["1"], num=3, status="queued", msg_id=None)
        db["entries"]["3"] = good
        # пост вийде, а після публікації впаде (нема автора) — помилка поза циклом повторів
        db["entries"]["2"] = {"num": 2, "status": "queued", "nom": "solo", "title": "x",
                              "photo": "p", "owner_tag": "@x"}
        await rt.post_queue.put(2)
        await rt.post_queue.put(3)
        task = asyncio.create_task(publisher.publisher_loop(tg.bot))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if good["status"] == "published":
                break
        task.cancel()
        assert good["status"] == "published", "одна зламана заявка не зупиняє чергу"
    run(go())


def test_entry_removed_while_posting_deletes_post(tg):
    async def go():
        await tg.setup_contest(OWNER)

        def remove_during_send(method):
            if method.chat_id < 0:
                db["entries"]["1"]["status"] = "removed"
        tg.fake.hooks["SendPhoto"] = remove_during_send
        await tg.submit_solo(ALICE, "a1", "Зникну")
        assert any(type(c).__name__ == "DeleteMessage" for c in tg.fake.calls)
    run(go())


def test_waits_for_internet_then_starts(tg):
    async def go():
        from halloween_bot import app
        fails = {"n": 0}

        def offline(method):
            if fails["n"] < 2:
                fails["n"] += 1
                raise ConnectionError("hotspot not ready")
        tg.fake.hooks["GetMe"] = offline
        said = []
        me = await app.wait_for_telegram(tg.bot, delay=0.001, say=said.append)
        assert me.username == "hw_test_bot" and len(said) == 2 and "Нема зв'язку" in said[0]
    run(go())


def test_bad_token_gives_clear_message(tg, capsys, monkeypatch):
    from aiogram.exceptions import TelegramUnauthorizedError
    from halloween_bot import app

    async def bad_main(token):
        raise TelegramUnauthorizedError(method=None, message="Unauthorized")
    monkeypatch.setattr(app, "main", bad_main)
    monkeypatch.setattr(builtins, "input", lambda *_: "")
    assert app.run() == 1
    assert "не прийняв токен" in capsys.readouterr().out


def test_log_file_with_rotation():
    import logging
    from halloween_bot import app
    settings.log_max_kb, settings.log_backups = 1, 2
    handler = app.setup_logging()
    try:
        log = logging.getLogger("halloween.test")
        for i in range(200):
            log.info("рядок %s %s", i, "x" * 40)
        handler.flush()
        all_text = "".join(p.read_text(encoding="utf-8") for p in settings.log_path.parent.glob("bot.log*"))
        assert "рядок 199" in all_text and "рядок 185" in all_text   # найсвіжіше збережено
        assert "рядок 0 " not in all_text                            # найстаріше викинуто, диск не забивається
        files = sorted(p.name for p in settings.log_path.parent.glob("bot.log*"))
        assert files == ["bot.log", "bot.log.1", "bot.log.2"], files
        assert settings.log_path.stat().st_size <= 1024 + 200
        log.info("кирилиця й емодзі 🎃")
        handler.flush()
        assert "кирилиця й емодзі 🎃" in settings.log_path.read_text(encoding="utf-8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_events_go_to_log(tg):
    import logging
    from halloween_bot import app
    handler = app.setup_logging()
    try:
        async def go():
            await tg.setup_contest(OWNER)
            await tg.submit_solo(ALICE, "a1", "Логований костюм")
            await tg.send(OWNER, "/del 1")
        run(go())
        handler.flush()
        text = settings.log_path.read_text(encoding="utf-8")
        assert "Заявка №1 від @alice_w" in text and "Опубліковано №1" in text and "видалив" in text
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_disable_quick_edit_is_safe_everywhere():
    """На Linux і в PyCharm (не справжня консоль) — просто False, без помилок."""
    assert system.disable_quick_edit() in (True, False)


def test_background_loop_survives_unexpected_error():
    """Запис на диск і бекапи — фонові задачі. Одна несподівана помилка не має їх зупинити."""
    import asyncio
    from halloween_bot.services.backups import forever
    calls = []

    async def step():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("щось дивне")

    async def go():
        task = asyncio.create_task(forever("тест", lambda: 0, step))
        for _ in range(50):
            await asyncio.sleep(0)
            if len(calls) >= 3:
                break
        task.cancel()
        assert len(calls) >= 3, "цикл зупинився після першої помилки"
    run(go())
