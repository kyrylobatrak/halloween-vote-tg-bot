"""Дані бота: `data.json` (стан конкурсу) + `votes.log` (журнал голосів, лише дописується).

Запис відкладений: `store.save()` лише позначає зміни, а `store.flush()` раз на кілька
секунд скидає їх на диск. На Windows файл може на мить тримати антивірус/індексатор,
тому всі записи робляться з повторами.
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import os
import time
import zipfile
from pathlib import Path

from .config import settings

log = logging.getLogger("halloween.storage")

DEFAULTS: dict = {
    "seq": 0,                 # останній виданий номер костюма
    "submit_open": False,
    "vote_open": False,
    "entries": {},            # "47": {...}
    "votes": {},              # "user_id": {"solo": [3, 47], "pair": [12]}
    "members": {},            # "user_id": true/false — підписка на канал (з подій каналу)
    "users": [],              # хто натискав /start (для /broadcast і /remind)
    "usernames": {},          # "dasha_k": 123456
    "names": {},              # "123456": "@dasha_k" або ім'я
    "banned": [],
    "banned_votes": {},       # "user_id": {"solo": [3]} — голоси забаненого, /unban їх повертає
    "owner": None,            # власник (/owner або OWNER_ID у .env)
    "admins": [],
    "channel_id": None,       # підхоплюється сам, коли бота роблять адміном каналу
    "channel_link": None,
    "channel_link_mode": None,  # open | request (вступ за заявкою) | primary
    "party_chat_id": None,    # чат туси: лише його учасники подають і голосують (підхоплюється сам)
    "party_title": None,
    "pending_channel": None,  # {"id","title","by"}: бота додали в канал до /owner — підключимо після
    "quiet_admins": [],       # адміни, яким шлемо лише підозрілі заявки (/quiet)
    "admin_log": [],          # [ts, actor_id, дія, деталі]
}


class StorageError(RuntimeError):
    """data.json пошкоджений, і відновити його з бекапу не вдалося."""


# ───────────── файлові операції з повторами ─────────────
def _retry(fn) -> None:
    for i in range(settings.io_retries):
        try:
            fn()
            return
        except PermissionError:
            if i == settings.io_retries - 1:
                raise
            time.sleep(settings.io_retry_delay)


def write_atomic(path: Path | str, data: bytes) -> None:
    """Запис через тимчасовий файл: навіть якщо ноут вимкнеться посеред запису,
    на диску лишиться або стара, або нова версія, але не половина."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")

    def go() -> None:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    _retry(go)


def append_lines(path: Path | str, rows: list) -> None:
    data = "".join(json.dumps(x) + "\n" for x in rows)

    def go() -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    _retry(go)


# ───────────── сховище ─────────────
class Store:
    def __init__(self) -> None:
        self.db: dict = {}
        self.vlog: list = []          # усі голоси (для /audit)
        self.pending: list = []       # голоси, ще не дописані у votes.log
        self.dirty = False
        self.version = 0              # скільки разів дані реально записано
        self.last_flush = 0.0
        self.last_error: str | None = None
        self.recovered_from: Path | None = None
        self.reset()

    # --- стан ---
    def reset(self) -> None:
        """Порожній стан. Об'єкт `db` той самий, тож посилання на нього лишаються дійсними."""
        self.db.clear()
        self.db.update(copy.deepcopy(DEFAULTS))
        self.vlog.clear()
        self.pending.clear()
        self.dirty = False
        self.version = 0
        self.last_flush = 0.0
        self.last_error = None
        self.recovered_from = None

    def load(self) -> None:
        self.reset()
        data = self._read_data()
        self.db.update(data)
        for k, v in DEFAULTS.items():
            self.db.setdefault(k, copy.deepcopy(v))
        self._load_votes_log()
        if settings.owner_id:
            self.db["owner"] = settings.owner_id     # .env головніший за data.json: бота не перехопити

    def _read_data(self) -> dict:
        path = settings.data_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            broken = path.with_name(time.strftime("data.broken_%m%d_%H%M%S.json"))
            os.replace(path, broken)
            log.error("data.json пошкоджений (%s), перейменовано на %s", ex, broken.name)
            for candidate in sorted(settings.backups_dir.glob("data_*.json"), reverse=True):
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                self.recovered_from = candidate
                self.dirty = True                     # одразу запишемо відновлену версію
                log.warning("Відновлено з бекапу %s", candidate.name)
                return data
            raise StorageError(
                f"data.json пошкоджений, а придатного бекапу в {settings.backups_dir} нема. "
                f"Пошкоджений файл збережено як {broken.name}. "
                "Розпакуй останній zip-бекап з Telegram у папку бота і запусти знову."
            ) from ex

    def _load_votes_log(self) -> None:
        path = settings.votes_log_path
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.vlog.append(json.loads(line))
                        except ValueError:
                            pass              # обірваний останній рядок після аварії
        old = self.db.pop("votes_log", None)  # перенесення зі старого формату (журнал усередині data.json)
        if old and not self.vlog:
            append_lines(path, old)
            self.vlog.extend(old)
            self.dirty = True

    # --- зміни ---
    def save(self) -> None:
        """Позначити, що дані змінились. На диск їх скидає flush()."""
        self.dirty = True

    # d у журналі голосів: 1 — голос, -1 — людина зняла голос сама,
    # 2 / -2 — голос повернула / зняла модерація (/restore, /unban / /del, /ban, вихід з конкурсу).
    # Тож «сума знаків» по костюму завжди = голосів зараз, а /audit бачить сплески лише справжніх тапів.
    VOTE, UNVOTE, RESTORED, REMOVED = 1, -1, 2, -2

    def log_vote(self, uid: int, nom: str, num: int, d: int) -> None:
        row = [int(time.time()), uid, nom, num, d]
        self.vlog.append(row)
        self.pending.append(row)

    def dump(self) -> bytes:
        return json.dumps(self.db, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # --- запис на диск ---
    async def flush(self) -> bool:
        """Записати зміни. True = успішно (або нічого записувати)."""
        if not self.dirty and not self.pending:
            return True
        rows = self.pending[:]
        del self.pending[:]
        was_dirty, self.dirty = self.dirty, False
        data = self.dump() if was_dirty else None    # серіалізуємо тут: дані не зміняться посеред запису
        try:
            if rows:
                try:
                    await asyncio.to_thread(append_lines, settings.votes_log_path, rows)
                except OSError:
                    self.pending[:0] = rows           # повернемо в чергу, допишемо наступного разу
                    raise
            if data is not None:
                await asyncio.to_thread(write_atomic, settings.data_path, data)
            self.version += 1
            self.last_flush = time.time()
            self.last_error = None
            return True
        except OSError as ex:
            self.dirty = self.dirty or was_dirty
            self.last_error = str(ex)
            log.error("Не вдалося записати дані: %s", ex)
            return False

    def flush_sync(self) -> None:
        """Останній запис при зупинці бота (поза циклом подій)."""
        if self.pending:
            append_lines(settings.votes_log_path, self.pending)
            del self.pending[:]
        if self.dirty:
            write_atomic(settings.data_path, self.dump())
            self.dirty = False

    # --- бекапи ---
    def backup_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("data.json", self.dump())
            z.writestr("votes.log", "".join(json.dumps(x) + "\n" for x in self.vlog))
        return buf.getvalue()

    def local_backup(self, data: bytes | None = None) -> None:
        """Копія в backups/. data — вже серіалізований стан (з циклу подій), щоб не читати db з потоку."""
        d = settings.backups_dir
        d.mkdir(parents=True, exist_ok=True)
        write_atomic(d / time.strftime("data_%m%d_%H%M%S.json"), data if data is not None else self.dump())
        if settings.votes_log_path.exists():   # журнал лише росте, тож тримаємо одну свіжу копію
            write_atomic(d / "votes.log", settings.votes_log_path.read_bytes())
        files = sorted(d.glob("data_*.json"))
        for f in files[:-settings.local_backups_keep]:
            try:
                f.unlink()
            except OSError:
                pass


store = Store()
db = store.db          # той самий словник назавжди: reset()/load() змінюють його на місці
