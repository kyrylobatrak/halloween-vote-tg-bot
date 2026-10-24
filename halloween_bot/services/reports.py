"""Звіти для адмінів: статистика, результати, аудит, журнали, стан бота."""
from __future__ import annotations

import csv
import io
import sys
import time
from collections import Counter

from aiogram import Bot

from ..config import NOMS, settings
from ..domain.people import name_of
from ..domain.results import bursts, current_voters, max_per_minute, ranking, tally
from ..runtime import rt
from ..storage import db, store
from ..ui.texts import ago, author_line, esc, hm
from .channel import missing_rights
from .moderation import drop_if_deleted


def stats_text() -> str:
    es = list(db["entries"].values())
    by = Counter((e["nom"], e["status"]) for e in es)
    voters = sum(1 for v in db["votes"].values() if any(v.values()))
    total = sum(len(lst) for v in db["votes"].values() for lst in v.values())
    unconfirmed = sum(1 for e in es if e["status"] == "waiting")
    lines = ["📊 <b>Статистика</b>"]
    for k, meta in NOMS.items():
        lines.append(f"{meta['name']}: {by[(k, 'published')]} опубл."
                     + (f", {by[(k, 'queued')]} у черзі" if by[(k, 'queued')] else ""))
    lines += [
        f"Видалено: {sum(by[(k, 'removed')] for k in NOMS)}, збоїв: {sum(by[(k, 'failed')] for k in NOMS)}",
        f"Проголосувало людей: {voters}, усього голосів: {total}",
        f"Пар чекають підтвердження партнера (ще не в каналі): {unconfirmed}" + (" · опублікувати без нього: /repost N" if unconfirmed else ""),
        f"Користувачів бота: {len(db['users'])}",
        f"Подача: {'🟢' if db['submit_open'] else '🔴'}  Голосування: {'🟢' if db['vote_open'] else '🔴'}",
    ]
    return "\n".join(lines)


async def results_text(bot: Bot, k: int) -> str:
    """Топ-k у кожній номінації. Пости претендентів перевіряються: видалені руками знімаються."""
    k = max(1, min(k, 30))
    lines = ["🏆 <b>Результати</b>"]
    dropped: list[int] = []
    checked: set[int] = set()
    for nom, meta in NOMS.items():
        while True:
            cnt = tally()
            es = ranking(nom, cnt)
            gone = []
            for e in es[:k]:
                if e["num"] not in checked:
                    checked.add(e["num"])
                    if await drop_if_deleted(bot, e):
                        gone.append(e["num"])
            dropped += gone
            if not gone:
                break
        lines.append(f"\n<b>{meta['name']}</b>")
        if not es:
            lines.append("заявок нема")
            continue
        for i, e in enumerate(es[:k], 1):
            lines.append(f"{i}. №{e['num']} «{esc(e['title'])}» · {author_line(e)} · <b>{cnt[e['num']]}</b>")
        if len(es) > 1 and cnt[es[0]["num"]] == cnt[es[1]["num"]]:
            lines.append("⚠️ Нічия за 1 місце!")
    if dropped:
        lines.append("\n🗑 Пости видалені з каналу вручну, тому їх знято з конкурсу: "
                     + ", ".join(f"№{n}" for n in dropped))
    return "\n".join(lines)


def audit_overview() -> str:
    rows = bursts(store.vlog, settings.burst_per_min)
    head = (f"🔎 <b>Аудит</b>: записів у журналі {len(store.vlog)}\n"
            f"Сплеск = від {settings.burst_per_min} голосів за 1 хв за один костюм\n")
    if not rows:
        return head + "\n✅ Підозрілих сплесків нема.\nДеталі по костюму: /audit 47"
    lines = [head] + [f"⚠️ №{n}: {p} голосів за хвилину (з {hm(at)})" for p, n, at in rows[:20]]
    return "\n".join(lines) + "\n\nДеталі: /audit N"


def audit_entry(e: dict) -> tuple[str, list[str]]:
    """(заголовок, рядки «час  хто») для /audit N."""
    num = e["num"]
    events = [x for x in store.vlog if x[3] == num]
    current = current_voters(num, e["nom"])
    last_plus: dict[int, int] = {}
    for ts, uid, _nom, _n, d in events:
        if d > 0:
            last_plus[uid] = ts
    peak, at = max_per_minute([x[0] for x in events if x[4] == 1])
    head = "\n".join([
        f"🔎 <b>Аудит №{num}</b> «{esc(e['title'])}» · {author_line(e)}",
        f"Статус: {e['status']}",
        f"Голосів зараз: <b>{len(current)}</b>",
        f"Подій у журналі: +{sum(1 for x in events if x[4] > 0)} / −{sum(1 for x in events if x[4] < 0)}",
        f"Пік: {peak} за хвилину" + (f" (з {hm(at)})" if peak else "")
        + (" ⚠️" if peak >= settings.burst_per_min else ""),
    ])
    voters = sorted(current, key=lambda uid: last_plus.get(uid, 0))
    vlines = [f"{hm(last_plus[uid]) if uid in last_plus else '--:--:--'}  {name_of(uid)}" for uid in voters]
    return head, vlines


ACTIONS = {1: "vote", -1: "unvote", 2: "restored (admin)", -2: "removed (admin)"}


def votelog_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "voter_id", "voter", "nomination", "entry", "action"])
    for ts, uid, nom, num, d in store.vlog:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), uid, name_of(uid),
                    nom, num, ACTIONS.get(d, str(d))])
    return ("﻿" + buf.getvalue()).encode("utf-8")       # BOM, щоб Excel показав кирилицю


def adminlog_text(k: int) -> str | None:
    rows = db["admin_log"][-max(1, min(k, 200)):]
    if not rows:
        return None
    return "\n".join(
        f"{time.strftime('%d.%m %H:%M', time.localtime(ts))}  "
        f"{'🤖 бот' if not uid else name_of(uid)}: {action} {detail}".strip()
        for ts, uid, action, detail in rows
    )


def remind_jobs(extra: str, text_fn, markup) -> tuple[list[tuple[int, str, dict]], int]:
    """(завдання розсилки, скільки людей пропущено, бо вже використали всі голоси)."""
    jobs, skipped = [], 0
    for uid in list(db["users"]):
        if uid in db["banned"]:
            continue
        if db["party_chat_id"] and rt.party_cache.get(uid, (True, 0))[0] is False:
            continue                                  # уже знаємо, що не гість туси
        mine = db["votes"].get(str(uid), {})
        left = {nom: settings.max_votes - len(mine.get(nom, [])) for nom in NOMS}
        if not any(left.values()):
            skipped += 1
            continue
        jobs.append((uid, text_fn(left, extra), {"reply_markup": markup}))
    return jobs, skipped


async def health_text(bot: Bot) -> str:
    up = int(time.time() - rt.started)
    t0 = time.perf_counter()
    try:
        await bot.get_me()
        tg = f"✅ {int((time.perf_counter() - t0) * 1000)} мс"
    except Exception as ex:
        tg = f"❌ {esc(str(ex))[:80]}"
    ch = "❌ не підключено"
    if db["channel_id"]:
        try:
            me = await bot.get_chat_member(db["channel_id"], bot.id)
            missing = missing_rights(me)
            ch = "✅ бот адмін" if me.status == "administrator" else f"⚠️ статус бота: {me.status}"
            if missing:
                ch += f", ⚠️ нема прав: {', '.join(missing)}"
        except Exception as ex:
            ch = f"❌ {esc(str(ex))[:80]}"
    failed = sum(1 for e in db["entries"].values() if e["status"] == "failed")
    size = settings.data_path.stat().st_size // 1024 if settings.data_path.exists() else 0
    lines = [
        "🩺 <b>Стан бота</b>",
        f"⏱ Працює: {up // 3600} год {up % 3600 // 60} хв",
        f"📡 Telegram: {tg}",
        f"📢 Канал: {ch}",
        f"💾 Запис на диск: {ago(store.last_flush)}" + (" · ⏳ є незаписані зміни" if store.dirty else "")
        + (" · 🆘 ПОМИЛКА ЗАПИСУ" if rt.save_alerted else ""),
        f"🗂 Бекап у backups/: {ago(rt.last_local_backup)} · у Telegram: {ago(rt.last_tg_backup)}",
        "🎟 Чат туси: " + (f"✅ «{esc(db['party_title'] or '')}», голосують лише його учасники"
                           if db["party_chat_id"] else "❌ не підключено (голосувати може будь-хто з каналу)"),
        f"📮 Черга постів: {rt.post_queue.qsize()}" + (f" · ⚠️ збоїв: {failed} (/repost N)" if failed else ""),
        f"📄 data.json: {size} КБ · журнал голосів: {len(store.vlog)}",
        f"😴 Захист від сну: {'✅' if rt.awake else '—'} · 🐍 Python {sys.version.split()[0]}",
        f"Подача: {'🟢' if db['submit_open'] else '🔴'}  Голосування: {'🟢' if db['vote_open'] else '🔴'}",
    ]
    if rt.errors:
        lines.append(f"🐞 Помилок в обробниках: {len(rt.errors)} (деталі в bot.log), остання:\n"
                     f"<code>{esc(rt.errors[-1])}</code>")
    return "\n".join(lines)
