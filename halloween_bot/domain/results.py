"""Підрахунок голосів, рейтинги й аналіз сплесків (для /results та /audit)."""
from __future__ import annotations

from collections import Counter

from ..storage import db


def tally() -> Counter:
    """Кількість поточних голосів за кожен номер."""
    return Counter(n for v in db["votes"].values() for lst in v.values() for n in lst)


def ranking(nom: str, cnt: Counter | None = None) -> list[dict]:
    """Опубліковані костюми номінації від найбільшої кількості голосів (при рівності — менший номер)."""
    cnt = tally() if cnt is None else cnt
    return sorted(
        (e for e in db["entries"].values() if e["nom"] == nom and e["status"] == "published"),
        key=lambda e: (-cnt[e["num"]], e["num"]),
    )


def max_per_minute(times: list[int]) -> tuple[int, int]:
    """(максимум подій за будь-які 60 с, час початку цього вікна)."""
    times = sorted(times)
    best, start, j = 0, 0, 0
    for i, t in enumerate(times):
        while t - times[j] >= 60:
            j += 1
        if i - j + 1 > best:
            best, start = i - j + 1, times[j]
    return best, start


def bursts(vlog: list, threshold: int) -> list[tuple[int, int, int]]:
    """[(пік голосів за хвилину, номер, час початку)] для костюмів зі сплеском ≥ threshold."""
    plus: dict[int, list[int]] = {}
    for ts, _uid, _nom, num, d in vlog:
        if d > 0:
            plus.setdefault(num, []).append(ts)
    rows = []
    for num, ts_list in plus.items():
        peak, at = max_per_minute(ts_list)
        if peak >= threshold:
            rows.append((peak, num, at))
    rows.sort(reverse=True)
    return rows


def current_voters(num: int, nom: str) -> list[int]:
    return [int(uid) for uid, v in db["votes"].items() if num in v.get(nom, [])]
