"""Адміни й власник теж учасники вечірки: подають костюми й голосують на загальних правилах."""
from conftest import ADMIN2, ALICE, BOBBY, OWNER, run
from halloween_bot.config import settings
from halloween_bot.storage import db


async def contest_with_admin(tg):
    await tg.setup_contest(OWNER)
    await tg.send(ADMIN2, "/start")
    await tg.send(OWNER, "/addadmin @adminka")
    settings.tap_guard = 0


def test_owner_and_admin_submit_and_vote(tg):
    async def go():
        await contest_with_admin(tg)
        n_owner = await tg.submit_solo(OWNER, "own1", "Граф Дракула")
        n_admin = await tg.submit_pair(ADMIN2, "@alice_w", "adm1", "Мортиша і Гомес")
        n_alice = await tg.submit_solo(ALICE, "al1", "Відьма")
        assert n_owner and n_admin and n_alice
        assert db["entries"][str(n_owner)]["status"] == "published"
        await tg.tap(ALICE, f"pc:{n_admin}")                     # партнерка підтверджує → пара йде в канал
        await tg.drain()
        assert db["entries"][str(n_admin)]["partner_id"] == ALICE["id"]
        await tg.member(OWNER["id"], ADMIN2["id"], BOBBY["id"])
        assert "зараховано" in await tg.vote(OWNER, n_alice)
        assert "зараховано" in await tg.vote(ADMIN2, n_owner)
        assert "свій" in await tg.vote(OWNER, n_owner)            # за себе — ні, як і всім
        assert "свій" in await tg.vote(ADMIN2, n_admin)
        assert "зараховано" in await tg.vote(BOBBY, n_admin)
        await tg.send(OWNER, "/results")
        assert "Граф Дракула" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_admin_entry_can_be_deleted_but_not_banned(tg):
    """Бан адміна-учасника — це по суті зняття з адмінки; спершу /deladmin, тоді бан."""
    async def go():
        await contest_with_admin(tg)
        n_admin = await tg.submit_solo(ADMIN2, "adm1", "Вампір")
        n_owner = await tg.submit_solo(OWNER, "own1", "Граф")
        await tg.send(OWNER, f"/ban {n_admin}")
        assert ADMIN2["id"] not in db["banned"] and db["entries"][str(n_admin)]["status"] == "published"
        assert "адмін" in tg.fake.last_to(OWNER["id"])
        await tg.send(ADMIN2, f"/ban {n_owner}")
        assert OWNER["id"] not in db["banned"] and db["entries"][str(n_owner)]["status"] == "published"
        await tg.tap(ADMIN2, f"ban:{n_owner}")
        assert OWNER["id"] not in db["banned"]
        await tg.send(OWNER, f"/del {n_admin}")                    # просто зняти костюм можна
        assert db["entries"][str(n_admin)]["status"] == "removed"
        await tg.send(OWNER, "/deladmin @adminka")
        await tg.send(OWNER, f"/restore {n_admin}")
        await tg.drain()
        await tg.send(OWNER, f"/ban {n_admin}")                    # тепер він звичайний учасник
        assert ADMIN2["id"] in db["banned"]
    run(go())


def test_ban_removes_all_votes_and_unban_returns_them(tg):
    """Сценарій 2: накрутник набрав голосів і його забанили — його голоси більше не рахуються."""
    from conftest import CAROL, DAVE

    async def go():
        await contest_with_admin(tg)
        n_bob = await tg.submit_solo(BOBBY, "b1", "Мумія")          # Боббі — накрутник, теж учасник
        n_alice = await tg.submit_solo(ALICE, "a1", "Відьма")
        n_carol = await tg.submit_solo(CAROL, "c1", "Зомбі")
        n_pair = await tg.submit_pair(DAVE, None, "d1", "Пара")
        await tg.send(OWNER, f"/repost {n_pair}")                   # адмін публікує пару без підтвердження
        await tg.drain()
        await tg.member(BOBBY["id"], ALICE["id"])
        for n in (n_alice, n_carol, n_pair):
            assert "зараховано" in await tg.vote(BOBBY, n)
        assert "зараховано" in await tg.vote(ALICE, n_bob)
        await tg.send(OWNER, f"/ban {n_bob}")
        assert BOBBY["id"] in db["banned"]
        assert str(BOBBY["id"]) not in db["votes"]                 # голоси Боббі зняті…
        assert db["banned_votes"][str(BOBBY["id"])] == {"solo": [n_alice, n_carol], "pair": [n_pair]}
        assert db["votes"][str(ALICE["id"])]["solo"] == []         # …і голоси ЗА його костюм теж
        await tg.send(OWNER, "/results")
        assert "<b>0</b>" in tg.fake.last_to(OWNER["id"]) and "<b>1</b>" not in tg.fake.last_to(OWNER["id"])
        minus = [r for r in tg_vlog() if r[1] == BOBBY["id"] and r[4] == -2]       # знято модерацією
        assert len(minus) == 3                                     # журнал голосів це бачить
        # розбан: голоси повертаються, крім тих, де костюм за цей час зняли
        await tg.send(OWNER, f"/del {n_carol}")
        await tg.send(OWNER, f"/unban {BOBBY['id']}")
        assert BOBBY["id"] not in db["banned"]
        assert db["votes"][str(BOBBY["id"])] == {"solo": [n_alice], "pair": [n_pair]}
        assert "повернуто: 2" in tg.fake.last_to(OWNER["id"])
        assert str(BOBBY["id"]) not in db["banned_votes"]
    run(go())


def test_restore_of_banned_entry_also_returns_authors_votes(tg):
    async def go():
        await contest_with_admin(tg)
        n_bob = await tg.submit_solo(BOBBY, "b1", "Мумія")
        n_alice = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.member(BOBBY["id"])
        await tg.vote(BOBBY, n_alice)
        await tg.send(OWNER, f"/ban {n_bob}")
        assert str(BOBBY["id"]) not in db["votes"]
        await tg.send(OWNER, f"/restore {n_bob}")
        assert BOBBY["id"] not in db["banned"]
        assert db["votes"][str(BOBBY["id"])]["solo"] == [n_alice]
        assert "голосів повернуто: 1" in tg.fake.last_to(OWNER["id"])
    run(go())


def tg_vlog():
    from halloween_bot.storage import store
    return store.vlog


def test_restore_does_not_revive_votes_of_banned_voter(tg):
    """Знайдено симуляцією: №X зняли → його виборця забанили → /restore X повертав голос забаненому."""
    from conftest import CAROL

    async def go():
        await contest_with_admin(tg)
        n_bob = await tg.submit_solo(BOBBY, "b1", "Мумія")
        n_alice = await tg.submit_solo(ALICE, "a1", "Відьма")
        n_carol = await tg.submit_solo(CAROL, "c1", "Зомбі")
        await tg.member(BOBBY["id"])
        await tg.vote(BOBBY, n_alice)
        await tg.vote(BOBBY, n_carol)
        await tg.send(OWNER, f"/del {n_alice}")                  # голос Боббі «запам'ятали» в №Аліси
        await tg.send(OWNER, f"/ban {n_bob}")
        await tg.send(OWNER, f"/restore {n_alice}")
        assert not any(db["votes"].get(str(BOBBY["id"]), {}).values())     # забанений — без голосів
        await tg.send(OWNER, f"/del {n_carol}")                  # поки Боббі в бані, зняли і №Керол
        await tg.send(OWNER, f"/unban {BOBBY['id']}")
        assert db["votes"][str(BOBBY["id"])]["solo"] == [n_alice]          # голос за №Аліси повернувся
        await tg.send(OWNER, f"/restore {n_carol}")
        assert sorted(db["votes"][str(BOBBY["id"])]["solo"]) == sorted([n_alice, n_carol])
    run(go())


def test_pair_published_only_after_partner_confirms(tg):
    """Пара чекає в боті, поки партнер не скаже «Так, це я». Попередження — на кожному кроці."""
    from aiogram.methods import SendPhoto
    from conftest import CAROL

    async def go():
        await contest_with_admin(tg)
        await tg.send(BOBBY, "/start")
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:pair")
        assert "лише після того, як партнер підтвердить" in tg.fake.last_to(ALICE["id"])
        await tg.send(ALICE, "@bobby_k")
        await tg.photo(ALICE, "pw1")
        await tg.send(ALICE, "Відьми")
        preview = [c for c in tg.fake.of(SendPhoto) if c.chat_id == ALICE["id"]][-1].caption
        assert "лише після того, як партнер підтвердить" in preview
        await tg.tap(ALICE, "pub")
        await tg.drain()
        e = db["entries"]["1"]
        assert e["status"] == "waiting" and not tg.fake.channel_posts()
        assert "щойно @bobby_k підтвердить" in tg.fake.last_to(ALICE["id"])
        assert "з'явиться в каналі, щойно ти підтвердиш" in tg.fake.last_to(BOBBY["id"])
        # «Мої костюми» показує, що пара чекає; голосувати ще нема за що
        await tg.tap(ALICE, "my")
        assert "чекає, поки партнер підтвердить" in tg.fake.of(SendPhoto)[-1].caption
        await tg.member(CAROL["id"])
        assert "ще не опубліковано" in await tg.vote(CAROL, 1)
        await tg.send(OWNER, "/stats")
        assert "чекають підтвердження партнера (ще не в каналі): 1" in tg.fake.last_to(OWNER["id"])
        # перезапуск не публікує пару, що чекає
        from halloween_bot.services.publisher import requeue_pending
        assert requeue_pending() == 0
        # «Це не я» — пара так і не з'являється, автор і адміни попереджені
        await tg.tap(BOBBY, "pd:1")
        await tg.drain()
        assert not tg.fake.channel_posts() and "не є твоїм партнером" in tg.fake.last_to(ALICE["id"])
        # потім Боббі передумав і підтвердив → пара в каналі, обоє отримали посилання
        await tg.tap(BOBBY, "pc:1")
        assert "Публікую пару в канал" in tg.fake.last_to(BOBBY["id"])
        await tg.drain()
        assert e["status"] == "published" and len(tg.fake.channel_posts()) == 1
        assert "опубліковано" in tg.fake.last_to(ALICE["id"]) and "опубліковано" in tg.fake.last_to(BOBBY["id"])
        assert "зараховано" in await tg.vote(CAROL, 1)
    run(go())


def test_withdraw_restore_and_force_publish_of_waiting_pair(tg):
    async def go():
        await contest_with_admin(tg)
        n = await tg.submit_pair(ALICE, None, "pw2", "Пара")
        assert db["entries"][str(n)]["status"] == "waiting"
        await tg.send(OWNER, f"/del {n}")
        await tg.send(OWNER, f"/restore {n}")
        assert db["entries"][str(n)]["status"] == "waiting"           # не в канал, а знову чекати партнера
        assert "знову чекає підтвердження" in tg.fake.last_to(OWNER["id"])
        await tg.drain()
        assert not tg.fake.channel_posts()
        await tg.send(OWNER, f"/repost {n}")                          # партнер без телефону — адмін публікує
        await tg.drain()
        assert db["entries"][str(n)]["status"] == "published" and len(tg.fake.channel_posts()) == 1
        assert "партнер ще не підтвердив" in tg.fake.channel_posts()[-1].caption
        n2 = await tg.submit_pair(BOBBY, None, "pw3", "Пара 2")
        await tg.tap(BOBBY, f"wd:{n2}")
        await tg.tap(BOBBY, f"wdy:{n2}")
        assert db["entries"][str(n2)]["status"] == "removed" and len(tg.fake.channel_posts()) == 1
    run(go())
