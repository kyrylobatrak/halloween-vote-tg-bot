"""Спільне для тестів: ізольована тимчасова папка, фейковий Telegram і справжній Dispatcher.

FakeTelegram підміняє HTTP-сесію aiogram: Bot надсилає «запити», а ми записуємо їх
і повертаємо правдоподібні відповіді. Апдейти подаються в справжній Dispatcher,
тож тести перевіряють і порядок роутерів, і фільтри, і FSM — як у живому боті.
"""
from __future__ import annotations

import asyncio
import copy
import io
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ── ізоляція: до імпорту пакета (налаштування читаються під час імпорту) ──
TEST_HOME = Path(tempfile.mkdtemp(prefix="halloween-test-"))
for k in list(os.environ):
    if k in {"OWNER_ID", "DATA_PATH", "VOTES_LOG_PATH", "BACKUPS_DIR", "LOCK_PATH", "MAX_VOTES",
             "REQUIRE_MEMBER", "TAP_GUARD_SECONDS", "POST_INTERVAL", "SEND_RATE", "CHANNEL_PER_MIN"}:
        del os.environ[k]
os.environ["HALLOWEEN_HOME"] = str(TEST_HOME)
os.environ["HALLOWEEN_ENV_FILE"] = str(TEST_HOME / ".env")
os.environ["BOT_TOKEN"] = "123456:" + "A" * 35
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery, CreateChatInviteLink, DeleteMessage, EditMessageCaption, EditMessageMedia,
    EditMessageReplyMarkup, ExportChatInviteLink, GetChatMember, GetFile, GetMe, SendDocument,
    SendMessage, SendPhoto, SetMyCommands, DeleteMyCommands, RevokeChatInviteLink,
    ApproveChatJoinRequest, DeclineChatJoinRequest,
)
from aiogram.types import (  # noqa: E402
    Chat, ChatInviteLink, ChatMemberAdministrator, ChatMemberLeft, ChatMemberMember, File, Message,
    PhotoSize, User,
)
from PIL import Image  # noqa: E402

from halloween_bot import app  # noqa: E402
from halloween_bot.config import settings  # noqa: E402
from halloween_bot.runtime import rt  # noqa: E402
from halloween_bot.services import publisher  # noqa: E402
from halloween_bot.storage import db, store  # noqa: E402

BOT_ID = 123456
CHANNEL = -1001234567890
PARTY = -1009876543210          # чат туси
SETTINGS_SNAPSHOT = copy.deepcopy(settings.__dict__)


def run(coro):
    return asyncio.run(coro)


def make_image(seed: int, noise: int = 0) -> bytes:
    """Детерміноване «фото» 90×90 з блоками; noise імітує перезняте фото."""
    rnd = random.Random(seed)
    base = [rnd.randint(0, 255) for _ in range(81)]
    img = Image.new("L", (90, 90))
    px = []
    for y in range(90):
        for x in range(90):
            v = base[(y // 10) * 9 + x // 10]
            if noise:
                v += random.Random(seed * 100003 + x * 90 + y).randint(-noise, noise)
            px.append(max(0, min(255, v)))
    img.putdata(px)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ───────────────────────── фейковий Telegram ─────────────────────────
_TAG = re.compile(r'<(/?)(b|i|u|s|a|code|pre|tg-spoiler)(\s+href="[^"<>]*")?>')
_ENTITY = re.compile(r"&(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")


def check_html(text: str) -> None:
    """Те саме, що робить Telegram з parse_mode=HTML: лише дозволені теги, збалансовані,
    а «<» і «&» — тільки як частина тегу чи сутності. Інакше — «can't parse entities»."""
    stack, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "<":
            m = _TAG.match(text, i)
            if not m:
                raise ValueError(f"can't parse entities: unsupported start tag at {i}: {text[i:i+20]!r}")
            closing, name = m.group(1), m.group(2)
            if closing:
                if not stack or stack.pop() != name:
                    raise ValueError(f"can't parse entities: unmatched </{name}>")
            else:
                stack.append(name)
            i = m.end()
        elif ch == "&":
            m = _ENTITY.match(text, i)
            if not m:
                raise ValueError(f"can't parse entities: bare & at {i}: {text[i:i+20]!r}")
            i = m.end()
        else:
            i += 1
    if stack:
        raise ValueError(f"can't parse entities: unclosed <{stack[-1]}>")


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.files: dict[str, bytes] = {}       # file_id → байти
        self.members: dict[int, str] = {}       # user_id → статус у каналі
        self.party: dict[int, str] = {}         # user_id → статус у чаті туси
        self.links: dict[str, bool] = {}        # посилання на канал → чи «за заявкою»
        self.revoked: set[str] = set()
        self.bot_rights = dict(can_post_messages=True, can_edit_messages=True,
                               can_delete_messages=True, can_invite_users=True)
        self.deleted: set[int] = set()          # message_id постів, видалених «руками»
        self.hooks: dict[str, object] = {}     # назва методу → fn(method) (може кинути виняток)
        self.delay = 0.0                        # імітація повільного хотспоту
        self._mid = 1000
        self._links = 0

    def _enforce_limits(self, method) -> None:
        """Як справжній Telegram: задовгі тексти й зламаний HTML — помилка 400."""
        def bad(msg):
            raise TelegramBadRequest(method=method, message=f"Bad Request: {msg}")

        def html_ok(text, parse_mode):
            if text is not None and parse_mode is not None:     # None = явний «без розмітки»
                try:
                    check_html(text)
                except ValueError as ex:
                    bad(str(ex))
        if isinstance(method, SendMessage):
            if not method.text.strip():
                bad("message text is empty")
            if len(method.text) > 4096:
                bad("message is too long")
            html_ok(method.text, method.parse_mode)
        for attr in ("caption",):
            cap = getattr(method, attr, None)
            if isinstance(method, (SendPhoto, SendDocument, EditMessageCaption)) and cap is not None:
                if len(cap) > 1024:
                    bad("message caption is too long")
                html_ok(cap, method.parse_mode)
        if isinstance(method, EditMessageMedia) and method.media.caption:
            if len(method.media.caption) > 1024:
                bad("message caption is too long")
            html_ok(method.media.caption, method.media.parse_mode)
        if isinstance(method, AnswerCallbackQuery) and method.text and len(method.text) > 200:
            bad("MESSAGE_TOO_LONG")

    def next_mid(self) -> int:
        self._mid += 1
        return self._mid

    async def close(self) -> None:
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        file_id = url.rsplit("/", 1)[-1]
        yield self.files[file_id]

    def _msg(self, chat_id: int, **kw) -> Message:
        ctype = "channel" if chat_id < 0 else "private"
        return Message(message_id=self.next_mid(), date=int(time.time()),
                       chat=Chat(id=chat_id, type=ctype, title="Costumes" if ctype == "channel" else None), **kw)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        self._enforce_limits(method)
        if self.delay:
            await asyncio.sleep(self.delay)
        hook = self.hooks.get(type(method).__name__)
        if hook:
            res = hook(method)
            if res is not None:
                return res
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="Bot", username="hw_test_bot")
        if isinstance(method, SendMessage):
            return self._msg(method.chat_id, text=method.text)
        if isinstance(method, SendPhoto):
            return self._msg(method.chat_id, caption=method.caption,
                             photo=[PhotoSize(file_id=str(method.photo), file_unique_id="u", width=1, height=1)])
        if isinstance(method, SendDocument):
            return self._msg(method.chat_id, caption=method.caption)
        if isinstance(method, EditMessageReplyMarkup):
            if method.message_id in self.deleted:
                raise TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
            if method.chat_id is not None and method.chat_id < 0:
                raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
            return True
        if isinstance(method, (AnswerCallbackQuery, EditMessageCaption, EditMessageMedia, DeleteMessage)):
            return True
        if isinstance(method, GetChatMember):
            if method.user_id == BOT_ID:
                return ChatMemberAdministrator(user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
                                               can_be_edited=False, is_anonymous=False, can_manage_chat=True,
                                               can_change_info=True, can_restrict_members=True,
                                               can_promote_members=False, can_manage_video_chats=True,
                                               can_post_stories=False, can_edit_stories=False,
                                               can_delete_stories=False, can_send_welcome_messages=False,
                                               **self.bot_rights)
            u = User(id=method.user_id, is_bot=False, first_name="U")
            if method.chat_id == PARTY:
                return ChatMemberMember(user=u) if self.party.get(method.user_id) == "member" \
                    else ChatMemberLeft(user=u)
            if self.members.get(method.user_id) == "member":
                return ChatMemberMember(user=u)
            return ChatMemberLeft(user=u)
        if isinstance(method, CreateChatInviteLink):
            self._links += 1
            self.links[f"https://t.me/+LINK{self._links}"] = bool(method.creates_join_request)
            return ChatInviteLink(invite_link=f"https://t.me/+LINK{self._links}", name=method.name,
                                  creator=User(id=BOT_ID, is_bot=True, first_name="Bot"),
                                  creates_join_request=False, is_primary=False, is_revoked=False)
        if isinstance(method, ExportChatInviteLink):
            return "https://t.me/+PRIMARY_REVOKES_OLD"
        if isinstance(method, RevokeChatInviteLink):
            self.revoked.add(method.invite_link)
            return ChatInviteLink(invite_link=method.invite_link, creator=User(id=BOT_ID, is_bot=True, first_name="B"),
                                  creates_join_request=False, is_primary=False, is_revoked=True)
        if isinstance(method, ApproveChatJoinRequest):
            self.members[method.user_id] = "member"
            return True
        if isinstance(method, DeclineChatJoinRequest):
            return True
        if isinstance(method, (SetMyCommands, DeleteMyCommands)):
            for cmd in getattr(method, "commands", []):
                if not re.fullmatch(r"[a-z0-9_]{1,32}", cmd.command) or not 1 <= len(cmd.description) <= 256:
                    raise TelegramBadRequest(method=method, message="Bad Request: BOT_COMMAND_INVALID")
            return True
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id + "_u", file_path=method.file_id)
        raise AssertionError(f"FakeTelegram: неочікуваний метод {type(method).__name__}")

    # ── зручні вибірки ──
    def of(self, cls) -> list:
        return [c for c in self.calls if isinstance(c, cls)]

    def texts_to(self, chat_id: int) -> list[str]:
        out = []
        for c in self.calls:
            if isinstance(c, SendMessage) and c.chat_id == chat_id:
                out.append(c.text)
            elif isinstance(c, (SendPhoto, SendDocument)) and c.chat_id == chat_id:
                out.append(c.caption or "")
        return out

    def last_to(self, chat_id: int) -> str:
        t = self.texts_to(chat_id)
        return t[-1] if t else ""

    def channel_posts(self) -> list[SendPhoto]:
        return [c for c in self.of(SendPhoto) if c.chat_id == CHANNEL]


# ───────────────────────── «клієнт» для тестів ─────────────────────────
class TG:
    """Шле апдейти в справжній Dispatcher від імені користувачів."""

    def __init__(self) -> None:
        self.fake = FakeTelegram()
        self.bot = app.create_bot(settings.bot_token, session=self.fake)
        self.dp = app.create_dispatcher()
        self._uid = 0
        self._cb = 0
        self.alerts: dict[str, tuple[int, str | None]] = {}

    @staticmethod
    def user(uid: int, username: str | None = "auto") -> dict:
        d = {"id": uid, "is_bot": False, "first_name": f"U{uid}"}
        if username == "auto":
            username = f"user{uid}x"
        if username:
            d["username"] = username
        return d

    async def _feed(self, update: dict) -> None:
        self._uid += 1
        update["update_id"] = self._uid
        await self.dp.feed_raw_update(self.bot, update)

    def _private(self, user: dict, **kw) -> dict:
        return {"message_id": self.fake.next_mid(), "date": int(time.time()),
                "chat": {"id": user["id"], "type": "private", "first_name": user["first_name"]},
                "from": user, **kw}

    async def send(self, user: dict, text: str) -> None:
        await self._feed({"message": self._private(user, text=text)})

    async def photo(self, user: dict, file_id: str, image: bytes | None = None,
                    media_group_id: str | None = None) -> None:
        self.fake.files.setdefault(file_id + "_s", image or make_image(abs(hash(file_id)) % 10000))
        extra = {"media_group_id": media_group_id} if media_group_id else {}
        await self._feed({"message": self._private(user, photo=[
            {"file_id": file_id + "_s", "file_unique_id": file_id + "_su", "width": 90, "height": 90},
            {"file_id": file_id, "file_unique_id": file_id + "_uid", "width": 800, "height": 800},
        ], **extra)})

    async def document(self, user: dict) -> None:
        await self._feed({"message": self._private(user, document={"file_id": "d", "file_unique_id": "du"})})

    async def tap(self, user: dict, data: str, in_channel: bool = False, msg_id: int | None = None,
                  old: bool = False) -> str | None:
        """Натиснути інлайн-кнопку. Повертає текст відповіді (спливаючого вікна), якщо був.
        old=True — кнопка під повідомленням, старшим за 48 год: Telegram шле його як «недоступне»
        (date=0, без тексту), і редагувати його бот уже не може."""
        self._cb += 1
        cid = f"cb{self._cb}"
        chat = ({"id": CHANNEL, "type": "channel", "title": "Costumes"} if in_channel
                else {"id": user["id"], "type": "private", "first_name": user["first_name"]})
        message = {"message_id": msg_id or self.fake.next_mid(), "date": int(time.time()), "chat": chat,
                   "text": "x"}
        if old:
            message = {"message_id": message["message_id"], "date": 0, "chat": chat}
        await self._feed({"callback_query": {"id": cid, "from": user, "chat_instance": "ci",
                                             "data": data, "message": message}})
        answers = [c for c in self.fake.of(AnswerCallbackQuery) if c.callback_query_id == cid]
        return answers[-1].text if answers else None

    async def vote(self, user: dict, num: int) -> str | None:
        return await self.tap(user, f"v:{num}", in_channel=True)

    async def chat_member(self, user_id: int, status: str) -> None:
        """Подія каналу: хтось вступив/вийшов."""
        u = self.user(user_id)
        await self._feed({"chat_member": {
            "chat": {"id": CHANNEL, "type": "channel", "title": "Costumes"}, "from": u,
            "date": int(time.time()),
            "old_chat_member": {"status": "left", "user": u},
            "new_chat_member": {"status": status, "user": u}}})

    async def bot_status(self, actor: dict, status: str, chat_id: int = CHANNEL, **rights) -> None:
        """Подія my_chat_member: бота зробили адміном / змінили права / прибрали."""
        botu = {"id": BOT_ID, "is_bot": True, "first_name": "Bot", "username": "hw_test_bot"}
        if status == "administrator":
            r = dict(can_post_messages=True, can_edit_messages=True, can_delete_messages=True,
                     can_invite_users=True)
            r.update(rights)
            new = {"status": "administrator", "user": botu, "can_be_edited": False, "is_anonymous": False,
                   "can_manage_chat": True, "can_change_info": True, "can_restrict_members": True,
                   "can_promote_members": False, "can_manage_video_chats": True,
                   "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False,
                   "can_send_welcome_messages": False, **r}
        else:
            new = {"status": status, "user": botu}
        await self._feed({"my_chat_member": {
            "chat": {"id": chat_id, "type": "channel", "title": "Costumes"}, "from": actor,
            "date": int(time.time()), "old_chat_member": {"status": "left", "user": botu},
            "new_chat_member": new}})

    async def party_bot(self, actor: dict, status: str = "administrator") -> None:
        """Бота додали в чат туси (status: member | administrator | left)."""
        botu = {"id": BOT_ID, "is_bot": True, "first_name": "Bot", "username": "hw_test_bot"}
        new = {"status": status, "user": botu}
        if status == "administrator":
            new.update(can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_change_info=False,
                       can_delete_messages=False, can_invite_users=False, can_restrict_members=False,
                       can_pin_messages=False, can_promote_members=False, can_manage_video_chats=False,
                       can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                       can_send_welcome_messages=False)
        await self._feed({"my_chat_member": {
            "chat": {"id": PARTY, "type": "supergroup", "title": "Halloween Party ММФ"}, "from": actor,
            "date": int(time.time()), "old_chat_member": {"status": "left", "user": botu},
            "new_chat_member": new}})

    async def guest(self, *uids: int, status: str = "member") -> None:
        """Людина вступила в чат туси (або вийшла: status="left") — подія chat_member."""
        for uid in uids:
            self.fake.party[uid] = status
            u = self.user(uid)
            await self._feed({"chat_member": {
                "chat": {"id": PARTY, "type": "supergroup", "title": "Halloween Party ММФ"}, "from": u,
                "date": int(time.time()), "old_chat_member": {"status": "left", "user": u},
                "new_chat_member": {"status": status, "user": u}}})

    async def join_request(self, user: dict) -> None:
        """Заявка на вступ у канал за посиланням «за заявкою»."""
        await self._feed({"chat_join_request": {
            "chat": {"id": CHANNEL, "type": "channel", "title": "Costumes"}, "from": user,
            "user_chat_id": user["id"], "date": int(time.time()),
            "invite_link": {"invite_link": "https://t.me/+X", "creator": {"id": BOT_ID, "is_bot": True,
                            "first_name": "Bot"}, "creates_join_request": True, "is_primary": False,
                            "is_revoked": False}}})

    async def drain(self) -> None:
        """Опублікувати все з черги (замість фонового publisher_loop)."""
        while not rt.post_queue.empty():
            await publisher.publish_one(self.bot, rt.post_queue.get_nowait())

    # ── готові сценарії ──
    async def setup_contest(self, owner: dict) -> None:
        """Власник, підключений канал, відкриті подача й голосування."""
        await self.send(owner, "/start")
        await self.send(owner, "/owner")
        await self.bot_status(owner, "administrator")
        db["submit_open"] = db["vote_open"] = True

    async def submit_solo(self, user: dict, photo: str, title: str) -> int | None:
        await self.send(user, "/start")
        await self.tap(user, "sub")
        await self.tap(user, "nom:solo")
        await self.photo(user, photo)
        await self.send(user, title)
        before = db["seq"]
        await self.tap(user, "pub")
        await self.drain()
        return db["seq"] if db["seq"] != before else None

    async def submit_pair(self, user: dict, partner: str | None, photo: str, title: str) -> int | None:
        await self.send(user, "/start")
        await self.tap(user, "sub")
        await self.tap(user, "nom:pair")
        if partner:
            await self.send(user, partner)
        else:
            await self.tap(user, "nouser")
        await self.photo(user, photo)
        await self.send(user, title)
        before = db["seq"]
        await self.tap(user, "pub")
        await self.drain()
        return db["seq"] if db["seq"] != before else None

    async def member(self, *uids: int) -> None:
        for uid in uids:
            self.fake.members[uid] = "member"
            await self.chat_member(uid, "member")


def close_file_logs() -> None:
    """app.run()/setup_logging() вішають на root-логер bot.log. Windows не дає видалити
    відкритий файл, тож перед прибиранням закриваємо всі файлові логери."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)
            h.close()


def open_files_in_test_home() -> list[str]:
    """Linux-аналог блокування Windows: які файли тестової папки досі відкриті цим процесом."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return []
    found = []
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith(str(TEST_HOME)):
            found.append(target)
    return found


@pytest.fixture(autouse=True)
def clean_state():
    """Кожен тест — з чистого аркуша: порожні дані, файли, черга, FSM, налаштування."""
    close_file_logs()
    for p in TEST_HOME.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    settings.__dict__.update(copy.deepcopy(SETTINGS_SNAPSHOT))
    settings.post_interval = 0.001          # тести не чекають 3.5 с між постами
    settings.send_rate = 2000
    store.reset()
    rt.reset()
    rt.bot_username = "hw_test_bot"
    publisher.RETRY_DELAY = 0.001
    dp = app.create_dispatcher()
    dp.storage.storage.clear()
    yield
    close_file_logs()
    leaked = open_files_in_test_home()
    assert not leaked, f"тест лишив відкриті файли (на Windows їх не видалити): {leaked}"
    assert not rt.errors, f"обробник впав (помилку проковтнув errors.router): {list(rt.errors)}"


@pytest.fixture
def tg() -> TG:
    return TG()


OWNER = TG.user(1, "kyrylo")
ALICE = TG.user(2, "alice_w")
BOBBY = TG.user(3, "bobby_k")
CAROL = TG.user(4, "carol_m")
DAVE = TG.user(5, "dave_r")
EVE = TG.user(6, "evelyn")
NONAME = TG.user(7, None)
VOTER = TG.user(8, "voter_one")
ADMIN2 = TG.user(9, "adminka")
