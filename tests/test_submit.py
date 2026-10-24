"""Подача заявок: соло, пари, підтвердження партнера, фото, альбоми, черга."""

from conftest import (ALICE, BOBBY, CAROL, CHANNEL, DAVE, EVE, NONAME, OWNER, VOTER,
                      make_image, run)
from halloween_bot.storage import db


def test_solo_published_with_vote_button_and_notifications(tg):
    async def go():
        await tg.setup_contest(OWNER)
        num = await tg.submit_solo(ALICE, "ph1", "Відьма з перездачею")
        assert num == 1
        e = db["entries"]["1"]
        assert e["status"] == "published" and e["msg_id"] and e["chat_id"] == CHANNEL
        post = tg.fake.channel_posts()[-1]
        assert "№1" in post.caption and "Відьма з перездачею" in post.caption and "#соло" in post.caption
        assert post.reply_markup.inline_keyboard[0][0].callback_data == "v:1"
        assert "опубліковано" in tg.fake.last_to(ALICE["id"])
        assert any("Відьма" in t for t in tg.fake.texts_to(OWNER["id"])), "адмін отримує копію заявки"
    run(go())


def test_title_is_html_escaped(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_solo(ALICE, "ph1", "<b>хак</b> & co")
        cap = tg.fake.channel_posts()[-1].caption
        assert "&lt;b&gt;хак&lt;/b&gt; &amp; co" in cap
    run(go())


def test_title_too_long_and_commands_inside_flow(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.send(ALICE, "просто текст")
        assert "саме фото" in tg.fake.last_to(ALICE["id"])
        await tg.document(ALICE)
        assert "а не файлом" in tg.fake.last_to(ALICE["id"])
        await tg.photo(ALICE, "ph1")
        await tg.send(ALICE, "x" * 51)
        assert "Задовго (51)" in tg.fake.last_to(ALICE["id"])
        await tg.send(ALICE, "/щось")
        assert "заверши подачу" in tg.fake.last_to(ALICE["id"])
        await tg.send(ALICE, "/myvotes")             # команди учасника працюють посеред подачі
        assert "Твої голоси" in tg.fake.last_to(ALICE["id"])
        await tg.send(ALICE, "Нормальна назва")
        await tg.tap(ALICE, "pub")
        await tg.drain()
        assert db["entries"]["1"]["title"] == "Нормальна назва"
    run(go())


def test_admin_commands_work_in_the_middle_of_submission(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.tap(OWNER, "sub")
        await tg.tap(OWNER, "nom:solo")
        await tg.send(OWNER, "/stats")
        assert "Статистика" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_one_solo_per_person_and_closed_submission(tg):
    async def go():
        await tg.setup_contest(OWNER)
        assert await tg.submit_solo(ALICE, "ph1", "Перший") == 1
        await tg.tap(ALICE, "sub")
        alert = await tg.tap(ALICE, "nom:solo")
        assert "вже є костюм у соло: №1" in alert
        db["submit_open"] = False
        assert "закритий" in await tg.tap(BOBBY, "sub")
    run(go())


def test_pair_partner_gets_confirmation_and_one_person_one_pair(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(BOBBY, "/start")                    # бот знає bobby → може написати йому
        num = await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Відьми")
        assert num == 1
        assert any("Це ти?" in t for t in tg.fake.texts_to(BOBBY["id"]))
        e = db["entries"]["1"]
        assert e["status"] == "waiting" and not tg.fake.channel_posts()       # до підтвердження — не в каналі
        assert "щойно @bobby_k підтвердить" in "".join(tg.fake.texts_to(ALICE["id"]))
        await tg.tap(BOBBY, "pc:1")
        await tg.drain()
        assert e["partner_id"] == BOBBY["id"] and e["status"] == "published"
        assert "@bobby_k" in tg.fake.channel_posts()[-1].caption
        # bobby (партнер) не може подати іншу пару, а carol не може взяти в пару alice
        await tg.tap(BOBBY, "sub")
        alert = await tg.tap(BOBBY, "nom:pair")
        assert "в парі №1 (партнер)" in alert
        await tg.tap(CAROL, "sub")
        await tg.tap(CAROL, "nom:pair")
        await tg.send(CAROL, "@alice_w")
        assert "вже є в парі №1" in tg.fake.last_to(CAROL["id"])
        await tg.send(CAROL, "@carol_m")
        assert "іншою людиною" in tg.fake.last_to(CAROL["id"])
        await tg.send(CAROL, "@ab")
        assert "Не схоже на username" in tg.fake.last_to(CAROL["id"])
    run(go())


def test_pair_without_username_confirmed_by_link(tg):
    async def go():
        await tg.setup_contest(OWNER)
        num = await tg.submit_pair(EVE, None, "pp2", "Пара без юзернейму")
        assert any("?start=p1" in t for t in tg.fake.texts_to(EVE["id"]))
        assert not tg.fake.channel_posts()
        await tg.send(NONAME, "/start p1")
        e = db["entries"][str(num)]
        assert e["partner_id"] is None, "без username — спершу схвалення автора"
        assert "Запит надіслано автору" in tg.fake.last_to(NONAME["id"])
        assert "хоче приєднатися" in tg.fake.last_to(EVE["id"])
        await tg.send(NONAME, "/start p1")
        assert "Запит уже надіслано" in tg.fake.last_to(NONAME["id"])
        await tg.tap(EVE, f"pa:1:{NONAME['id']}")
        assert e["partner_id"] == NONAME["id"] and e["partner_name"] == "U7"
        assert "Автор підтвердив" in "".join(tg.fake.texts_to(NONAME["id"]))
        await tg.drain()
        assert "U7" in tg.fake.channel_posts()[-1].caption and e["status"] == "published"
        await tg.send(VOTER, "/start p1")
        assert "вже є підтверджений" in tg.fake.last_to(VOTER["id"])
        await tg.send(EVE, "/start p1")
        assert "твоя ж заявка" in tg.fake.last_to(EVE["id"])
    run(go())


def test_partner_link_is_only_for_named_partner(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Пара")
        await tg.send(CAROL, "/start p1")
        assert "для @bobby_k" in tg.fake.last_to(CAROL["id"])
        assert db["entries"]["1"]["partner_id"] is None
    run(go())


def test_partner_deny_notifies_owner_and_admins(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(BOBBY, "/start")
        await tg.submit_pair(ALICE, "@bobby_k", "pp1", "Пара")
        await tg.tap(BOBBY, "pd:1")
        assert "не є твоїм партнером" in tg.fake.last_to(ALICE["id"])
        assert "відмовився" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_exact_duplicate_photo_rejected_and_similar_flagged(tg):
    async def go():
        await tg.setup_contest(OWNER)
        img = make_image(42)
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.photo(ALICE, "orig", img)
        await tg.send(ALICE, "Оригінал")
        await tg.tap(ALICE, "pub")
        await tg.drain()
        # той самий файл
        await tg.tap(DAVE, "sub")
        await tg.tap(DAVE, "nom:solo")
        await tg.photo(DAVE, "orig", img)
        assert "вже подавали в конкурс: №1" in tg.fake.last_to(DAVE["id"])
        # перезняте (схоже) фото: публікується, але адмін бачить попередження
        await tg.photo(DAVE, "reshot", make_image(42, noise=3))
        await tg.send(DAVE, "Схоже")
        await tg.tap(DAVE, "pub")
        await tg.drain()
        assert db["entries"]["2"]["similar_to"] == 1
        assert "схоже на №1" in tg.fake.last_to(OWNER["id"])
    run(go())


def test_album_only_first_photo_is_used(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.send(ALICE, "/start")
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        for i in range(3):
            await tg.photo(ALICE, f"alb{i}", media_group_id="G1")
        texts = tg.fake.texts_to(ALICE["id"])
        assert sum("Як називається" in t for t in texts) == 1
        assert sum("Напиши назву текстом" in t for t in texts) == 0, "решта альбому тихо ігнорується"
        assert any("беру перше" in t for t in texts)
        await tg.send(ALICE, "Альбомна")
        await tg.tap(ALICE, "pub")
        await tg.drain()
        assert db["entries"]["1"]["photo"] == "alb0"
    run(go())


def test_queue_position_shown_when_many_submit(tg):
    async def go():
        await tg.setup_contest(OWNER)
        users = [tg.user(100 + i) for i in range(6)]
        for i, u in enumerate(users):                    # подають, поки черга не публікується
            await tg.send(u, "/start")
            await tg.tap(u, "sub")
            await tg.tap(u, "nom:solo")
            await tg.photo(u, f"q{i}")
            await tg.send(u, f"Костюм {i}")
            await tg.tap(u, "pub")
        assert "6-й у черзі" in tg.fake.last_to(users[-1]["id"])
        assert "кілька секунд" in tg.fake.last_to(users[0]["id"])
        await tg.drain()
        assert len(tg.fake.channel_posts()) == 6
    run(go())


def test_cancel_resets_flow(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.tap(ALICE, "sub")
        await tg.tap(ALICE, "nom:solo")
        await tg.send(ALICE, "/cancel")
        await tg.photo(ALICE, "ph1")
        assert "Тисни /start" in tg.fake.last_to(ALICE["id"])
        assert db["seq"] == 0
    run(go())


def test_banned_cannot_submit(tg):
    async def go():
        await tg.setup_contest(OWNER)
        db["banned"].append(ALICE["id"])
        assert "не можеш подавати" in await tg.tap(ALICE, "sub")
    run(go())


def test_leaked_link_stranger_rejected_by_author(tg):
    """Посилання «втекло» в загальний чат: чужий не потрапить у пару без схвалення автора."""
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_pair(EVE, None, "pp2", "Пара")
        stranger, real = tg.user(70), NONAME
        await tg.send(stranger, "/start p1")
        await tg.send(real, "/start p1")
        assert "не твоя пара" in await tg.tap(ALICE, f"pa:1:{stranger['id']}"), "схвалює лише автор"
        await tg.tap(EVE, f"pr:1:{stranger['id']}")
        assert "не підтвердив тебе" in tg.fake.last_to(stranger["id"])
        assert db["entries"]["1"]["partner_id"] is None
        await tg.tap(EVE, f"pa:1:{real['id']}")
        assert db["entries"]["1"]["partner_id"] == real["id"]
        await tg.tap(EVE, f"pa:1:{stranger['id']}")
        assert "неактуальний" in tg.fake.last_to(EVE["id"])
    run(go())


def test_approval_rechecks_one_person_one_pair(tg):
    async def go():
        await tg.setup_contest(OWNER)
        await tg.submit_pair(EVE, None, "pp2", "Пара Еви")
        await tg.send(NONAME, "/start p1")
        await tg.submit_pair(NONAME, "@carol_m", "pp3", "Власна пара")   # поки Ева думала
        await tg.tap(EVE, f"pa:1:{NONAME['id']}")
        assert "Не можу додати" in tg.fake.last_to(EVE["id"]) and "в парі №2" in tg.fake.last_to(EVE["id"])
        assert db["entries"]["1"]["partner_id"] is None
    run(go())


def test_cancel_after_publish_explains_how_to_withdraw(tg):
    """Справжній випадок з тесту: костюм опублікували, потім натиснули «Скасувати» — і чекали, що він зникне."""
    async def go():
        await tg.setup_contest(OWNER)
        n = await tg.submit_solo(ALICE, "a1", "Відьма")
        await tg.tap(ALICE, "cancel")
        assert "Мої костюми" in tg.fake.last_to(ALICE["id"]) and "Зняти" in tg.fake.last_to(ALICE["id"])
        await tg.send(ALICE, "/cancel")
        assert "скасовувати нічого" in tg.fake.last_to(ALICE["id"])
        assert db["entries"][str(n)]["status"] == "published"
        await tg.send(ALICE, "/start")                       # посеред подачі /cancel — звичайне «Скасовано.»
        await tg.tap(ALICE, "sub")
        await tg.send(ALICE, "/cancel")
        assert tg.fake.last_to(ALICE["id"]) == "Скасовано."
    run(go())


def test_own_withdrawn_photo_can_be_resubmitted_but_not_by_others(tg):
    """Справжній випадок з тесту: автор сам зняв костюм і хоче подати те саме фото знову."""
    async def go():
        await tg.setup_contest(OWNER)
        n = await tg.submit_solo(ALICE, "same", "Відьма")
        await tg.tap(ALICE, f"wd:{n}")
        await tg.tap(ALICE, f"wdy:{n}")
        assert db["entries"][str(n)]["status"] == "removed"
        assert await tg.submit_solo(BOBBY, "same", "Крадене") is None          # чужому — ні
        assert "вже подавали" in "".join(tg.fake.texts_to(BOBBY["id"]))
        n2 = await tg.submit_solo(ALICE, "same", "Відьма 2.0")                   # авторці — так
        assert n2 and db["entries"][str(n2)]["status"] == "published"
        # костюм, знятий адміном: те саме фото не пускаємо навіть автору (для цього є /restore)
        await tg.send(OWNER, f"/del {n2}")
        assert await tg.submit_solo(ALICE, "same", "Відьма 3.0") is None
    run(go())
