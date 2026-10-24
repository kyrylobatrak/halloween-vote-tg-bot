"""Подача заявки: номінація → (партнер) → фото → назва → підтвердження."""
from __future__ import annotations

import logging

from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import NOMS, TITLE_MAX, USERNAME_RE
from ..domain import entries
from ..domain.entries import active, entry_block, partner_block, submit_block_reason
from ..domain.people import remember, tag_of
from ..domain.photos import dhash, find_dup
from ..services.party import party_gate
from ..services.publisher import accept_entry
from ..ui.keyboards import main_kb, no_username_kb, nominations_kb, publish_kb
from ..ui.texts import PAIR_WAIT, cancel_text, caption, esc, preview_note
from ..services.messaging import set_buttons
from .filters import private_router
from .states import Sub

log = logging.getLogger("halloween.submit")
router = private_router("submit")


async def check_photo(bot: Bot, m: Message, exclude: int | None = None):
    """(photo_uid, phash, similar_to) або None, якщо це точна копія вже поданого фото."""
    puid = m.photo[-1].file_unique_id
    try:
        buf = await bot.download(m.photo[0].file_id)       # найменша мініатюра, ~1-2 КБ
        ph = dhash(buf.read())
    except Exception as ex:
        log.warning("dhash failed: %s", ex)
        ph = None
    dup, exact = find_dup(puid, ph, exclude=exclude, user_id=m.from_user.id)
    if exact:
        where = f"№{dup['num']}" if active(dup) else f"№{dup['num']}, його зняли адміни (помилка? хай повернуть: /restore {dup['num']})"
        await m.answer(f"🙅 Це фото вже подавали в конкурс: {where}. Надішли власне фото костюма.")
        return None
    return puid, ph, (dup["num"] if dup else None)


@router.callback_query(F.data == "sub")
async def sub_start(c: CallbackQuery, state: FSMContext, bot: Bot):
    remember(c.from_user)
    if reason := submit_block_reason(c.from_user) or await party_gate(bot, c.from_user):
        return await c.answer(reason, show_alert=True)
    free = [k for k in NOMS if not entry_block(c.from_user, k)]
    if not free:
        return await c.answer("Ти вже береш участь в обох номінаціях 🎃", show_alert=True)
    await state.set_state(Sub.nom)            # подача почалась: /cancel тепер має що скасовувати
    await c.message.answer("Обери номінацію:", reply_markup=nominations_kb(free))
    await c.answer()


@router.callback_query(F.data.startswith("nom:"))
async def sub_nom(c: CallbackQuery, state: FSMContext):
    nom = c.data[4:]
    if nom not in NOMS:
        return await c.answer()
    if reason := submit_block_reason(c.from_user, nom):
        return await c.answer(reason, show_alert=True)
    await state.clear()
    await state.update_data(nom=nom)
    await set_buttons(c.message, None)
    if nom == "pair":
        await state.set_state(Sub.partner)
        await c.message.answer(f"👥 Напиши @username свого партнера по костюму.\n\n{PAIR_WAIT}\n\n/cancel щоб скасувати",
                               reply_markup=no_username_kb())
    else:
        await state.set_state(Sub.photo)
        await c.message.answer("📸 Надішли <b>одне фото</b> свого костюма.\n\n/cancel щоб скасувати")
    await c.answer()


@router.message(Sub.partner, F.text)
async def sub_partner(m: Message, state: FSMContext):
    if m.text.startswith("/"):
        return await m.answer("Спершу заверши подачу або натисни /cancel")
    mt = USERNAME_RE.match(m.text.strip())
    if not mt:
        return await m.answer("Не схоже на username 🤔 Приклад: <code>@dasha_k</code>")
    partner = mt.group(1)
    if reason := partner_block(m.from_user, partner):
        return await m.answer(esc(reason))
    await state.update_data(partner=partner)
    await state.set_state(Sub.photo)
    await m.answer("📸 Тепер надішли <b>одне фото</b>, де видно ваш парний костюм.")


@router.callback_query(Sub.partner, F.data == "nouser")
async def sub_partner_nouser(c: CallbackQuery, state: FSMContext):
    await state.update_data(partner=None)
    await state.set_state(Sub.photo)
    await set_buttons(c.message, None)
    await c.message.answer("Ок! Після публікації я дам тобі посилання, яке треба переслати партнеру. "
                           "Коли він його відкриє, тобі прийде запит, і ти підтвердиш, що це справді він.\n\n"
                           "📸 Тепер надішли <b>одне фото</b>, де видно ваш парний костюм.")
    await c.answer()


@router.message(Sub.partner)
async def sub_partner_bad(m: Message):
    await m.answer("Напиши @username текстом або натисни «У партнера нема username».")


@router.message(Sub.photo, F.photo)
async def sub_photo(m: Message, state: FSMContext, bot: Bot):
    res = await check_photo(bot, m)
    if res is None:
        return
    puid, ph, similar = res
    await state.update_data(photo=m.photo[-1].file_id, photo_uid=puid, phash=ph, similar_to=similar)
    await state.set_state(Sub.title)
    if m.media_group_id:
        await m.answer("📎 Ти надіслав кілька фото: беру перше, решту пропускаю.")
    await m.answer(f"✍️ Як називається твій образ? (до {TITLE_MAX} символів)\n"
                   "Наприклад: <i>Відьма з перездачею</i>")


@router.message(Sub.photo, F.document)
async def sub_photo_doc(m: Message):
    await m.answer("Надішли як <b>фото</b>, а не файлом 🙂")


@router.message(Sub.photo)
async def sub_photo_bad(m: Message):
    await m.answer("Потрібне саме фото 📸")


@router.message(Sub.title, F.text)
async def sub_title(m: Message, state: FSMContext):
    if m.text.startswith("/"):
        return await m.answer("Спершу заверши подачу або натисни /cancel")
    title = " ".join(m.text.split())
    if not title:
        return await m.answer("Назва не може бути порожньою.")
    if len(title) > TITLE_MAX:
        return await m.answer(f"Задовго ({len(title)}). Скороти до {TITLE_MAX} символів.")
    await state.update_data(title=title)
    await state.set_state(Sub.confirm)
    d = await state.get_data()
    preview = {"num": None, "title": title, "nom": d["nom"], "partner": d.get("partner"),
               "owner_tag": tag_of(m.from_user)}
    await m.answer_photo(
        d["photo"],
        caption=caption(preview) + preview_note(d["nom"]),
        reply_markup=publish_kb(),
    )


@router.message(Sub.title)
async def sub_title_bad(m: Message):
    await m.answer("Напиши назву текстом ✍️")


@router.callback_query(Sub.confirm, F.data == "pub")
async def sub_publish(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "photo_uid" not in d:                 # подвійний тап: перший уже опублікував і очистив стан
        return await c.answer("Вже прийнято 👌")
    await state.clear()                       # одразу, до будь-яких await: другий тап сюди не пройде
    u = c.from_user
    await set_buttons(c.message, None)
    # повторні перевірки: поки людина писала назву, хтось міг подати те саме
    reason = submit_block_reason(u, d["nom"]) or await party_gate(bot, u)
    if not reason and d.get("partner"):
        reason = partner_block(u, d["partner"])
    if not reason:
        dup, exact = find_dup(d["photo_uid"], None, user_id=u.id)
        if exact:
            reason = f"Це фото вже подали (№{dup['num']})."
    if reason:
        await c.message.answer(esc(reason), reply_markup=main_kb())
        return await c.answer()

    e = entries.create(u, tag_of(u), d)
    log.info("Заявка №%s від %s (%s): «%s»", e["num"], tag_of(u), e["nom"], e["title"])
    await c.message.answer(await accept_entry(bot, e), reply_markup=main_kb())
    await c.answer()


@router.callback_query(F.data == "cancel")
async def sub_cancel(c: CallbackQuery, state: FSMContext):
    await c.message.answer(cancel_text(await state.get_state()), reply_markup=main_kb())
    await state.clear()
    await set_buttons(c.message, None)
    await c.answer()
