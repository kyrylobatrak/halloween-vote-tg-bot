"""Тексти й форматування (HTML-розмітка Telegram)."""
from __future__ import annotations

import html
import time

from ..config import NOMS, settings
from ..runtime import rt
from ..storage import db

def esc(text) -> str:
    """Екранування для parse_mode=HTML. Telegram вимагає замінювати лише <, > і &; апостроф
    (Мар'яна, Сім'я Адамсів) і лапки лишаємо як є — без зайвих сутностей на кшталт &#x27;."""
    return html.escape(str(text), quote=False)


# ───────────── костюм ─────────────
def partner_tag(e: dict) -> str:
    if e.get("partner"):
        return "@" + e["partner"]
    return e.get("partner_name") or ""


def author_line(e: dict) -> str:
    s = esc(e["owner_tag"])
    if e["nom"] == "pair":
        pt = partner_tag(e)
        s += f" + {esc(pt)}" if pt else " + <i>(партнер ще не підтвердив)</i>"
    return s


def caption(e: dict) -> str:
    num = f"№{e['num']}" if e.get("num") else "№…"
    return (
        f"<b>{num}</b> · «{esc(e['title'])}»\n"
        f"{NOMS[e['nom']]['name']}\n"
        f"{author_line(e)}\n\n"
        f"{NOMS[e['nom']]['tag']}"
    )


def entry_label(e: dict) -> str:
    """Короткий опис для журналів (без HTML)."""
    return f"№{e['num']} «{e['title']}» ({e['owner_tag']})"


def similar_note(e: dict) -> str:
    s = e.get("similar_to")
    return f"\n\n⚠️ <b>Фото схоже на №{s}</b>, перевір, чи не крадене" if s else ""


# ───────────── посилання ─────────────
def post_link(e: dict) -> str:
    chat_id = e.get("chat_id") or db["channel_id"]
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{e['msg_id']}"


def confirm_link(num: int) -> str:
    return f"https://t.me/{rt.bot_username}?start=p{num}"


# ───────────── загальні тексти ─────────────
def welcome() -> str:
    return (
        "🎃 <b>Конкурс костюмів Halloween Party!</b>\n\n"
        "Номінації:\n"
        f"{NOMS['solo']['name']}\n{NOMS['pair']['name']}\n\n"
        "📸 <b>Подати костюм:</b> кнопка нижче, далі фото і назва образу. "
        "Костюм одразу з'явиться в каналі.\n\n"
        "🗳 <b>Голосувати:</b> підпишись на канал і тисни «Голосувати» під костюмами, які сподобались. "
        f"До {settings.max_votes} голосів у кожній номінації. Повторний тап знімає голос.\n\n"
        "💡 У каналі тапни #соло або #пари, щоб бачити лише одну номінацію."
    )


def channel_announce(key: str, value: bool) -> str:
    return {
        ("submit_open", True): "📸 Прийом костюмів відкрито! Подавай свій через бота.",
        ("submit_open", False): "📸 Прийом костюмів закрито. Голосування триває!",
        ("vote_open", True): "🗳 Голосування відкрито! Тисни «Голосувати» під костюмами: "
                             f"до {settings.max_votes} голосів у кожній номінації.",
        ("vote_open", False): "⛔ Голосування завершено! Результати оголосять на сцені 🎃",
    }[(key, value)]


ADMIN_HELP = (
    "<b>Адмін-команди</b>\n"
    "/open_submit · /close_submit: прийом заявок\n"
    "/open_vote · /close_vote: голосування\n"
    "/stats: статистика\n"
    "/results [N]: топ-N у кожній номінації (за замовч. 5)\n"
    "/broadcast текст: розсилка всім, хто стартанув бота\n"
    "/del N · /ban N: видалити костюм №N (і забанити автора)\n"
    "/unban user_id · /repost N\n"
    "/backup: надіслати бекап (data.json + votes.log)\n"
    "/quiet: тихий режим (лише підозрілі заявки)\n"
    "/verify: перевірити, чи не видалили пости з каналу руками\n"
    "/restore N: повернути видалений костюм разом з голосами\n"
    "/health: стан бота (зв'язок, канал, записи, бекапи)\n\n"
    "<b>Чесність</b>\n"
    "/audit: сплески голосів · /audit N: хто голосує за №N\n"
    "/votelog: журнал усіх голосів (CSV для Excel)\n"
    "/remind [текст]: персонально нагадати, скільки голосів лишилось\n\n"
    "<b>Тільки власник</b>\n"
    "/addadmin @username · /deladmin @username · /admins\n"
    "/adminlog [N]: журнал дій адмінів\n"
    "/newlink: нове посилання на канал (старі не анулюються)\n\n"
    "<b>Канал</b>: додай бота адміном у канал, і він підключиться сам\n"
    "<b>Чат туси</b>: додай бота в чат гостей — брати участь зможуть лише вони (адміни завжди)"
)

OWNER_GREETING = (
    "👑 Тепер ти власник бота!\n\n"
    "<b>Що далі:</b>\n"
    "1. Створи канал і додай туди бота <b>адміністратором</b>. Я сам його підхоплю.\n"
    "2. Додай інших адмінів: хай вони натиснуть /start, а ти напиши "
    "<code>/addadmin @їхній_username</code>\n"
    "3. Додай бота в <b>чат туси</b> (краще адміном, права не потрібні). Тоді подавати й голосувати "
    "зможуть лише його учасники, а посилання на канал стане «за заявкою».\n"
    "4. Перед подією: /open_submit та /open_vote\n\n"
    "Усі команди: /admin"
)


# ───────────── час ─────────────
def ago(ts: float) -> str:
    if not ts:
        return "ще не було"
    d = int(time.time() - ts)
    return f"{d} с тому" if d < 90 else f"{d // 60} хв тому"


def hm(ts: int) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


# ───────────── мої голоси ─────────────
def my_votes_text(uid: int) -> str:
    mine = db["votes"].get(str(uid), {})
    lines = ["🗳 <b>Твої голоси</b>"]
    for nom, meta in NOMS.items():
        nums = mine.get(nom, [])
        lines.append(f"\n{meta['name']}: <b>{len(nums)}/{settings.max_votes}</b>")
        for n in nums:
            e = db["entries"].get(str(n))
            if not e:
                continue
            t = f"№{n} «{esc(e['title'])}»"
            lines.append(f"• <a href=\"{post_link(e)}\">{t}</a>" if e.get("msg_id") else f"• {t}")
    if not any(mine.get(n) for n in NOMS):
        lines.append("\nТи ще не голосував. Заходь у канал і тисни «🗳 Голосувати» під костюмами!")
    else:
        lines.append("\nЩоб зняти голос, тапни «Голосувати» під цим костюмом ще раз.")
    return "\n".join(lines)


def reminder_text(left: dict[str, int], extra: str = "") -> str:
    lines = ["⏰ <b>Голосування ще триває!</b>"]
    if extra:
        lines.append(esc(extra))
    lines.append("\nУ тебе лишилось голосів:")
    for nom, meta in NOMS.items():
        lines.append(f"{meta['name']}: <b>{left[nom]}</b> з {settings.max_votes}")
    lines.append("\nЗазирни в канал і підтримай костюми, які сподобались 👇")
    return "\n".join(lines)


def cancel_text(state: str | None) -> str:
    """/cancel і «❌ Скасувати» скасовують лише незавершену подачу. Опублікований костюм так не зняти,
    тож коли скасовувати нічого, підказуємо, де це робиться."""
    if state:
        return "Скасовано."
    return ("Зараз нема незавершеної подачі, скасовувати нічого.\n"
            "Щоб зняти вже опублікований костюм: <b>📋 Мої костюми</b> → <b>🗑 Зняти</b> → <b>✅ Так, зняти</b>.")


PAIR_WAIT = "⏳ Парний костюм з'явиться в каналі лише після того, як партнер підтвердить участь."


def preview_note(nom: str) -> str:
    """Підпис під прев'ю перед «Опублікувати»."""
    note = ("\n\n<i>Так виглядатиме пост. Натискаючи «Опублікувати», ти погоджуєшся "
            "на публікацію фото та свого імені в каналі конкурсу.</i>")
    return note + (f"\n\n<b>{PAIR_WAIT}</b>" if nom == "pair" else "")
