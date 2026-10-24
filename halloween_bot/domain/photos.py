"""Відбитки фото: точні копії (той самий файл у Telegram) і схожі (перезняті/скріни)."""
from __future__ import annotations

import io

from PIL import Image

from ..config import settings
from ..storage import db
from .entries import active


def dhash(data: bytes) -> str:
    """64-бітний difference hash: однакові/перезняті фото дають майже той самий хеш."""
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = img.tobytes()                       # 1 байт на піксель (режим L); працює в усіх версіях Pillow
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def own_withdrawn(e: dict, user_id: int | None) -> bool:
    """Людина сама зняла свій костюм (або партнер її пари) — своє фото вона може подати знову."""
    return (user_id is not None and e["status"] == "removed" and e.get("removed_by") == "self"
            and user_id in (e["owner"], e.get("partner_id")))


def find_dup(photo_uid: str, phash: str | None, exclude: int | None = None, user_id: int | None = None):
    """(заявка, точна_копія) або (None, False).

    Точна копія шукається серед усіх заявок (і видалених теж: фото знятого адміном костюма
    не подати вдруге), схожа — лише серед активних. Виняток: власний костюм, який людина зняла сама.
    """
    best = None
    for e in db["entries"].values():
        if e["num"] == exclude or own_withdrawn(e, user_id):
            continue
        if e.get("photo_uid") == photo_uid:
            return e, True
        if phash and e.get("phash") and active(e):
            d = distance(phash, e["phash"])
            if d <= settings.similar_dist and (best is None or d < best[1]):
                best = (e, d)
    return (best[0], False) if best else (None, False)
