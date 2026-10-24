"""Порядок роутерів важливий: апдейт іде до першого обробника, чиї фільтри підійшли.

1. common      — альбоми (перше фото далі, решта ігнор), /start, /cancel, /owner, партнери
2. admin, moderation, reports, broadcast — команди: йдуть ДО кроків подачі,
   тож адмін може написати /stats навіть посеред подачі заявки
3. personal    — «Мої костюми», «Мої голоси», заміна фото
4. submit      — кроки подачі (тут є «ловці» будь-якого тексту в стані)
5. voting, channel — кнопки голосування й події каналу
6. fallback    — «Тисни /start» і відповідь на застарілі кнопки, завжди останні
7. errors      — будь-яка неперехоплена помилка: лог, «щось пішло не так» людині, лічильник у /health
"""
from __future__ import annotations

from aiogram import Router

from . import admin, broadcast, channel, common, errors, fallback, moderation, personal, reports, submit, voting


def routers() -> list[Router]:
    return [
        common.router,
        admin.router,
        moderation.router,
        reports.router,
        broadcast.router,
        personal.router,
        submit.router,
        voting.router,
        channel.router,
        fallback.router,
        fallback.callbacks,
        errors.router,
    ]
