"""Налаштування з .env і змінних середовища.

Модулі читають `settings.<поле>` у момент виклику (а не копіюють у свої константи),
тож тести й `/owner` можуть змінювати налаштування на льоту.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent

NOMS = {
    "solo": {"name": "👤 Індивідуальний костюм", "short": "соло", "tag": "#соло"},
    "pair": {"name": "👥 Парний костюм", "short": "пари", "tag": "#пари"},
}
TOKEN_RE = re.compile(r"^\d{5,}:[\w-]{30,}$")
USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{4,32})$")
TITLE_MAX = 50


def _raw(name: str) -> str:
    """Значення без коментаря в кінці рядка (`MAX_VOTES=7  # голосів` → `7`)."""
    return os.getenv(name, "").split("#", 1)[0].strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_raw(name))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_raw(name))
    except ValueError:
        return default


def _path(name: str, default: Path, base: Path) -> Path:
    v = _raw(name)
    if not v:
        return default
    p = Path(v)
    return p if p.is_absolute() else base / p


@dataclass
class Settings:
    env_file: Path
    data_dir: Path
    data_path: Path
    votes_log_path: Path
    backups_dir: Path
    lock_path: Path
    log_path: Path

    bot_token: str
    owner_id: int

    max_votes: int
    require_member: bool
    max_replaces: int
    similar_dist: int
    burst_per_min: int
    tap_guard: float

    post_interval: float
    channel_per_min: int
    send_rate: float

    save_every: float
    local_backup_every: float
    local_backups_keep: int
    vote_backup_every: float
    backup_every: float

    log_max_kb: int = 1024          # розмір одного файлу логу
    log_backups: int = 5            # скільки старих файлів тримати (bot.log.1 … bot.log.5)
    io_retries: int = 15
    io_retry_delay: float = 0.2


def load_settings() -> Settings:
    env_file = Path(os.getenv("HALLOWEEN_ENV_FILE") or PROJECT_DIR / ".env")
    load_dotenv(env_file, override=False)
    data_dir = Path(os.getenv("HALLOWEEN_HOME") or PROJECT_DIR)
    return Settings(
        env_file=env_file,
        data_dir=data_dir,
        data_path=_path("DATA_PATH", data_dir / "data.json", data_dir),
        votes_log_path=_path("VOTES_LOG_PATH", data_dir / "votes.log", data_dir),
        backups_dir=_path("BACKUPS_DIR", data_dir / "backups", data_dir),
        lock_path=_path("LOCK_PATH", data_dir / "bot.lock", data_dir),
        log_path=_path("LOG_PATH", data_dir / "bot.log", data_dir),
        bot_token=_raw("BOT_TOKEN"),
        owner_id=_int("OWNER_ID", 0),
        max_votes=_int("MAX_VOTES", 7),
        require_member=_raw("REQUIRE_MEMBER") != "0",
        max_replaces=_int("MAX_REPLACES", 3),
        similar_dist=_int("SIMILAR_DIST", 6),
        burst_per_min=_int("BURST_PER_MIN", 8),
        tap_guard=_float("TAP_GUARD_SECONDS", 5),
        post_interval=_float("POST_INTERVAL", 3.5),
        channel_per_min=_int("CHANNEL_PER_MIN", 19),
        send_rate=_float("SEND_RATE", 25),
        save_every=_float("SAVE_SECONDS", 2),
        local_backup_every=_float("LOCAL_BACKUP_MINUTES", 1) * 60,
        local_backups_keep=_int("LOCAL_BACKUPS_KEEP", 120),
        vote_backup_every=_float("VOTE_BACKUP_MINUTES", 3) * 60,
        backup_every=_float("BACKUP_MINUTES", 15) * 60,
        log_max_kb=_int("LOG_MAX_KB", 1024),
        log_backups=_int("LOG_BACKUPS", 5),
    )


settings = load_settings()


def append_env(key: str, value: str) -> None:
    """Дописати `KEY=value` у .env (токен, OWNER_ID), щоб пережити перезапуск."""
    settings.env_file.parent.mkdir(parents=True, exist_ok=True)
    with open(settings.env_file, "a", encoding="utf-8") as f:
        f.write(f"\n{key}={value}\n")
