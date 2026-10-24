"""Робота з комп'ютером: токен, захист від подвійного запуску, захист від сну."""
from __future__ import annotations

import os

from ..config import TOKEN_RE, append_env, settings


class AlreadyRunning(RuntimeError):
    pass


def ensure_token(ask=input, say=print) -> str:
    """Якщо токена нема в .env — спитати й зберегти. Повертає валідний токен."""
    token = settings.bot_token
    while not TOKEN_RE.match(token or ""):
        say("\n🎃 Встав токен бота від @BotFather і натисни Enter:")
        token = ask("> ").strip()
        if TOKEN_RE.match(token):
            append_env("BOT_TOKEN", token)
            say("✅ Токен збережено в .env, більше питати не буду.\n")
        else:
            say("❌ Не схоже на токен. Він виглядає так: 1234567890:AAH...")
    settings.bot_token = token
    return token


def acquire_lock():
    """Не дати запустити бота двічі на одному компі: два процеси перетирали б data.json.
    Повертає відкритий файл (тримати до кінця роботи) або кидає AlreadyRunning."""
    settings.lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(settings.lock_path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as ex:
        f.close()
        raise AlreadyRunning from ex
    return f


def keep_awake() -> bool:
    """Windows: не засинати від бездіяльності, поки бот працює (закриту кришку це не рятує)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required))
    except Exception:
        return False


def disable_quick_edit() -> bool:
    """Windows: вимкнути «режим виділення» (QuickEdit) у вікні бота.
    Інакше випадковий клік мишкою в чорне вікно ставить бота на паузу: консоль чекає,
    поки виділення скасують, а бот завмирає на першому ж рядку логу. Повертає True, якщо вимкнуто."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)                  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                                     # не консоль (PyCharm, перенаправлення)
        enable_quick_edit, enable_extended_flags = 0x0040, 0x0080
        new_mode = (mode.value & ~enable_quick_edit) | enable_extended_flags
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False
