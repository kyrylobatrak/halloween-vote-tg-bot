from aiogram.fsm.state import State, StatesGroup


class Sub(StatesGroup):
    """Кроки подачі заявки та заміни фото."""
    nom = State()          # обирає номінацію
    partner = State()
    photo = State()
    title = State()
    confirm = State()
    replace = State()
