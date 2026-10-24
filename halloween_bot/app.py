"""Збирання бота й запуск."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

from .config import settings
from .handlers import routers
from .runtime import rt
from .services.backups import backup_loop, flush_and_alert, saver_loop
from .services.commands import sync_commands
from .services.publisher import publisher_loop, requeue_pending
from .services.system import AlreadyRunning, acquire_lock, disable_quick_edit, ensure_token, keep_awake
from .storage import StorageError, db, store

log = logging.getLogger("halloween")

_dispatcher: Dispatcher | None = None

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging() -> logging.Handler:
    """Лог у консоль і у файл bot.log з ротацією: коли файл доростає до log_max_kb,
    він стає bot.log.1, попередній — bot.log.2 і т.д. (тримаємо log_backups штук)."""
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_path, maxBytes=settings.log_max_kb * 1024,
        backupCount=settings.log_backups, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_handler)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)   # без рядка на кожен апдейт
    return file_handler


def create_dispatcher() -> Dispatcher:
    """Один диспетчер на процес (роутер aiogram можна підключити лише до одного батька)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.include_routers(*routers())
    return _dispatcher


def create_bot(token: str, **kw) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), **kw)


async def wait_for_telegram(bot: Bot, delay: float = 5, say=print):
    """Інтернет на хотспоті міг ще не піднятися: чекаємо, а не падаємо."""
    while True:
        try:
            return await bot.get_me()
        except TelegramUnauthorizedError:
            raise
        except Exception as ex:
            log.warning("Нема зв'язку з Telegram на старті: %s", ex)
            say(f"📡 Нема зв'язку з Telegram ({type(ex).__name__}). Пробую ще раз через {delay:g} с…")
            await asyncio.sleep(delay)


async def startup_report(bot: Bot) -> None:
    me = await wait_for_telegram(bot)
    rt.bot_username = me.username or ""
    await sync_commands(bot)
    log.info("Старт: @%s, костюмів %s, голосів у журналі %s", me.username, len(db["entries"]), len(store.vlog))
    print(f"\n🎃 Бот @{me.username} запущено! Посилання: https://t.me/{me.username}")
    if store.recovered_from:
        print(f"♻️ data.json був пошкоджений — дані відновлено з бекапу {store.recovered_from.name}")
    if db["owner"] is None:
        print("👉 Відкрий бота в Telegram і напиши /owner, щоб стати власником.")
    if not db["channel_id"]:
        print("👉 Додай бота адміністратором у свій канал, і він підключиться сам.")
    else:
        try:
            ch = await bot.get_chat(db["channel_id"])
            print(f"📢 Канал: {ch.title}")
        except Exception as ex:
            log.error("Немає доступу до каналу: %s (бот має бути адміном)", ex)
    rt.awake = keep_awake()
    if rt.awake:
        print("😴 Комп не засне, поки бот працює (але кришку ноута не закривай!).")
    n = requeue_pending()
    if n:
        print(f"📮 У черзі на публікацію з минулого запуску: {n}")
    print("Щоб зупинити бота, натисни Ctrl+C (так дані гарантовано збережуться).\n")


async def main(token: str) -> None:
    bot = create_bot(token)
    dp = create_dispatcher()
    await startup_report(bot)
    tasks = [asyncio.create_task(publisher_loop(bot)),
             asyncio.create_task(backup_loop(bot)),
             asyncio.create_task(saver_loop(bot))]
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        await flush_and_alert(None)
        await bot.session.close()


def _pause() -> None:
    try:
        input("Натисни Enter, щоб закрити це вікно…")
    except EOFError:
        pass


def run() -> int:
    setup_logging()
    try:
        lock = acquire_lock()
    except AlreadyRunning:
        print("\n⛔ Бот уже запущено в іншому вікні (або в PyCharm). Другий екземпляр не потрібен.")
        _pause()
        return 1
    try:
        store.load()
    except StorageError as ex:
        print(f"\n⛔ {ex}")
        _pause()
        return 1
    token = ensure_token()                     # тут ще потрібна вставка правою кнопкою (QuickEdit)
    disable_quick_edit()                       # далі клік у вікно не ставить бота на паузу
    code = 0
    try:
        asyncio.run(main(token))
    except KeyboardInterrupt:
        pass
    except TelegramUnauthorizedError:
        log.error("Telegram не прийняв токен")
        print("\n⛔ Telegram не прийняв токен. Відкрий файл .env, видали рядок BOT_TOKEN=... "
              "і запусти знову: бот попросить новий токен від @BotFather.")
        _pause()
        code = 1
    finally:
        try:
            store.flush_sync()
            log.info("Зупинка: дані збережено")
            print("💾 Дані збережено. Бувай!")
        except OSError as ex:
            print(f"⚠️ Не вдалося зберегти дані при виході: {ex}")
        lock.close()
    return code
