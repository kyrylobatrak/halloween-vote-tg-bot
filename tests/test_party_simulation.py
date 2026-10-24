"""Стрес-тест «ціла вечірка»: сотні випадкових дій гостей і адмінів, перезапуск з диска,
і після кожного етапу — перевірка, що правила конкурсу не порушено.

Фейковий Telegram відхиляє задовгі тексти й зламаний HTML, тож будь-яке неекрановане ім'я
чи надто довгий звіт тут теж впаде.
"""
import json
import random

from aiogram.methods import AnswerCallbackQuery

from conftest import OWNER, TG, run
from halloween_bot import app
from halloween_bot.config import settings
from halloween_bot.domain.entries import active
from halloween_bot.domain.people import is_admin
from halloween_bot.runtime import rt
from halloween_bot.storage import Store, db, store

NASTY = ["<Діма & Co>", "Ярослав</b>", "Оля \"Відьма\"", "Мар'яна", "Сім'я Адамсів", "В'ячеслав 'Граф'", "Я" * 64, "🎃🦇👻", "a&amp;b", "<i>курсив", "Тарас"]


def make_people(rnd: random.Random, n: int) -> list[dict]:
    people = []
    for i in range(n):
        u = {"id": 1000 + i, "is_bot": False, "first_name": rnd.choice(NASTY)}
        if rnd.random() < 0.5:
            u["last_name"] = rnd.choice(NASTY)
        if rnd.random() < 0.75:
            u["username"] = f"guest{i}_x"
        people.append(u)
    return people


def check_invariants(tg: TG) -> None:
    entries = db["entries"]
    # 1. ліміт голосів, без дублів, лише за живі костюми, не за себе
    for uid, by_nom in db["votes"].items():
        for nom, nums in by_nom.items():
            assert len(nums) <= settings.max_votes, (uid, nom, nums)
            assert len(nums) == len(set(nums)), (uid, nums)
            for n in nums:
                e = entries[str(n)]
                assert e["nom"] == nom, (uid, n)
                assert e["status"] in ("published", "queued"), (uid, n, e["status"])
                assert e["owner"] != int(uid), f"{uid} голосує за себе (№{n})"
                assert e.get("partner_id") != int(uid), f"{uid} голосує за свою пару (№{n})"
    # 2. одна людина = одна пара; одне соло на людину
    in_pairs, solo_owners = {}, set()
    for e in entries.values():
        if not active(e):
            continue
        if e["nom"] == "solo":
            assert e["owner"] not in solo_owners, f"два соло в {e['owner']}"
            solo_owners.add(e["owner"])
        else:
            for p in filter(None, (e["owner"], e.get("partner_id"))):
                assert p not in in_pairs, f"{p} у парах №{in_pairs[p]} і №{e['num']}"
                in_pairs[p] = e["num"]
    # 3. у забанених нема жодного голосу, що рахується; адмінів не банять
    for uid in db["banned"]:
        assert not any(db["votes"].get(str(uid), {}).values()), f"забанений {uid} має голоси"
        assert not is_admin(uid), f"забанено адміна {uid}"
    # 5. якщо підключено чат туси — голоси й костюми лише в гостей і адмінів
    if db["party_chat_id"]:
        def allowed(uid):
            # "left" — був гостем і вийшов: старі голоси лишаються (нові вже не дасть)
            return is_admin(int(uid)) or tg.fake.party.get(int(uid)) in ("member", "left")
        for uid, by_nom in db["votes"].items():
            if any(by_nom.values()):
                assert allowed(uid), f"не-гість {uid} голосує"
        for e in entries.values():
            if active(e):
                assert allowed(e["owner"]), f"не-гість {e['owner']} подав №{e['num']}"
                if e.get("partner_id"):
                    assert allowed(e["partner_id"]), f"не-гість {e['partner_id']} у парі №{e['num']}"
    # 4. кожна кнопка отримала відповідь
    answered = {c.callback_query_id for c in tg.fake.of(AnswerCallbackQuery)}
    missing = [f"cb{i}" for i in range(1, tg._cb + 1) if f"cb{i}" not in answered]
    assert not missing, f"кнопки без відповіді: {missing[:5]}"


async def restart(tg: TG) -> None:
    """Перезапуск бота: дані — лише те, що на диску; FSM і кеші в пам'яті зникають."""
    assert await store.flush()
    snapshot = json.loads(json.dumps(db))
    vlog = list(store.vlog)
    store.load()
    assert json.loads(json.dumps(db)) == snapshot, "після перезапуску дані не ті самі"
    assert store.vlog == vlog, "журнал голосів після перезапуску не той самий"
    other = Store()
    other.load()
    assert other.db == db
    app.create_dispatcher().storage.storage.clear()
    username = rt.bot_username
    rt.reset()
    rt.bot_username = username
    from halloween_bot.services.publisher import requeue_pending
    requeue_pending()
    await tg.drain()


async def random_phase(tg: TG, rnd: random.Random, people: list[dict], admins: list[dict], steps: int) -> None:
    pool = people + admins                     # адміни й власник теж подають і голосують
    for _ in range(steps):
        u = rnd.choice(pool)
        roll = rnd.random()
        published = [e for e in db["entries"].values() if e["status"] == "published"]
        if roll < 0.18:                                           # подати соло
            await tg.send(u, "/start")
            await tg.tap(u, "sub")
            await tg.tap(u, "nom:solo")
            await tg.photo(u, f"s{u['id']}_{rnd.randint(0, 3)}")   # іноді — те саме фото вдруге
            await tg.send(u, rnd.choice(NASTY)[:50])
            await tg.tap(u, "pub")
            if rnd.random() < 0.2:
                await tg.tap(u, "pub")                             # подвійний тап
        elif roll < 0.30:                                         # подати пару
            partner = rnd.choice(people)
            await tg.send(u, "/start")
            await tg.tap(u, "sub")
            await tg.tap(u, "nom:pair")
            if partner.get("username") and rnd.random() < 0.7:
                await tg.send(u, "@" + partner["username"])
            else:
                await tg.tap(u, "nouser")
            await tg.photo(u, f"p{u['id']}_{rnd.randint(0, 3)}")
            await tg.send(u, rnd.choice(NASTY)[:50])
            before = db["seq"]
            await tg.tap(u, "pub")
            await tg.drain()
            num = db["seq"] if db["seq"] != before else None
            e = db["entries"].get(str(num)) if num else None
            if e and e.get("partner") and rnd.random() < 0.7:      # названий партнер відповідає
                await tg.tap(partner, f"{'pc' if rnd.random() < 0.85 else 'pd'}:{num}")
            elif e and not e.get("partner") and rnd.random() < 0.7:  # партнер без username просить
                await tg.send(partner, f"/start p{num}")
                if e.get("partner_requests"):
                    await tg.tap(u, f"{'pa' if rnd.random() < 0.8 else 'pr'}:{num}:{e['partner_requests'][0]}")
        elif roll < 0.38 and published:                           # партнер підтверджує / просить
            e = rnd.choice(published)
            if e["nom"] == "pair":
                await tg.send(u, f"/start p{e['num']}")
                await tg.tap(u, f"pc:{e['num']}")
                if e.get("partner_requests"):
                    owner = next((p for p in people if p["id"] == e["owner"]), None)
                    if owner:
                        await tg.tap(owner, f"{rnd.choice(['pa', 'pr'])}:{e['num']}:{e['partner_requests'][0]}")
        elif roll < 0.75 and published:                           # голосувати
            if rnd.random() < 0.7:
                await tg.member(u["id"])
            e = rnd.choice(published)
            await tg.vote(u, e["num"])
            if rnd.random() < 0.15:
                await tg.vote(u, e["num"])                         # подвійний тап
        elif roll < 0.80:                                         # мої костюми / голоси / замінити / зняти
            await tg.tap(u, rnd.choice(["my", "myv"]))
            mine = [e for e in db["entries"].values() if e["owner"] == u["id"] and active(e)]
            if mine:
                e = rnd.choice(mine)
                if rnd.random() < 0.5:
                    await tg.tap(u, f"rp:{e['num']}")
                    await tg.photo(u, f"r{u['id']}_{rnd.randint(0, 9)}")
                else:
                    await tg.tap(u, f"wd:{e['num']}")
                    await tg.tap(u, f"wdy:{e['num']}")
        elif roll < 0.88:                                         # адмінські дії
            a = rnd.choice(admins)
            cmd = rnd.choice(["/stats", "/results", "/results 30", "/audit", "/health", "/admins",
                              "/del", "/ban", "/ban", "/restore", "/quiet", "/audit", "/unban"])
            if cmd in ("/del", "/ban", "/restore", "/audit"):
                cmd += f" {rnd.randint(1, max(1, db['seq']))}"
            if cmd == "/unban":
                cmd += f" {rnd.choice(db['banned'] or [0])}"
            await tg.send(a, cmd)
            if published and rnd.random() < 0.2:                  # кнопки модерації під заявкою
                await tg.tap(a, f"{rnd.choice(['del', 'ban'])}:{rnd.choice(published)['num']}")
        else:                                                     # сміття й застарілі кнопки
            await tg.tap(u, rnd.choice(["pub", "nouser", "cancel", "v:999", "zzz", f"wdy:{rnd.randint(1, 50)}"]))
            await tg.send(u, rnd.choice(["привіт", "/cancel", "/myid", "<b>", "&"]))
        await tg.drain()


def test_whole_party_simulation(tg):
    simulate(tg, party=False)


def test_whole_party_simulation_only_guests(tg):
    """Те саме, але підключено чат туси: ~30% людей — «ліві» з пересланим посиланням.
    Частину з них орги посеред вечора додають у чат, частина гостей з чату виходить."""
    simulate(tg, party=True)


def simulate(tg, party: bool):
    async def go():
        rnd = random.Random(20261101)
        settings.tap_guard = 0
        settings.channel_per_min = 10**6       # ліміт каналу перевіряють окремі тести
        admin2 = TG.user(2, "admin_two")
        admin3 = TG.user(3, None)
        await tg.setup_contest(OWNER)
        for a in (admin2, admin3):
            await tg.send(a, "/start")
        await tg.send(OWNER, "/addadmin @admin_two")
        await tg.send(OWNER, "/addadmin 3")
        people = make_people(rnd, 120)
        admins = [OWNER, admin2, admin3]
        if party:
            rnd.seed(777)
            await tg.party_bot(OWNER)
            await tg.guest(*[p["id"] for p in people if rnd.random() < 0.7])

        await random_phase(tg, rnd, people, admins, 450)
        check_invariants(tg)
        await restart(tg)
        if party:
            outsiders = [p["id"] for p in people if tg.fake.party.get(p["id"]) != "member"]
            await tg.guest(*outsiders[: len(outsiders) // 2])                 # орги додали в чат
            await tg.guest(*[p["id"] for p in people[:10]], status="left")   # хтось вийшов з чату
        await random_phase(tg, rnd, people, admins, 450)
        check_invariants(tg)

        await tg.send(OWNER, "/close_submit")
        await tg.send(OWNER, "/remind Останні хвилини!")
        await random_phase(tg, rnd, people, admins, 150)
        await tg.send(OWNER, "/close_vote")
        for cmd in ("/audit", "/results", "/results 30", "/stats", "/votelog", "/backup", "/adminlog 200", "/verify"):
            await tg.send(OWNER, cmd)
        check_invariants(tg)
        await restart(tg)
        check_invariants(tg)

        published = sum(1 for e in db["entries"].values() if e["status"] == "published")
        votes = sum(len(v) for by in db["votes"].values() for v in by.values())
        assert published >= 30 and votes >= 100, (published, votes)   # симуляція справді щось робила
        assert "Результати" in "".join(tg.fake.texts_to(OWNER["id"]))
    run(go())
