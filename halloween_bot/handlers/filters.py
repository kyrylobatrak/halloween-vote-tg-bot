"""Фільтри й фабрика роутерів."""
from __future__ import annotations

from aiogram import F, Router

from ..domain.people import is_admin, is_owner

IsAdmin = F.from_user.id.func(is_admin)
IsOwner = F.from_user.id.func(is_owner)


def private_router(name: str) -> Router:
    """Роутер, у якому повідомлення обробляються лише в особистому чаті з ботом."""
    router = Router(name=name)
    router.message.filter(F.chat.type == "private")
    return router
