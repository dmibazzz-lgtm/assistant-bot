"""UI-клавиатуры бота Нова.

Сюда вынесены все «чистые» клавиатуры — те, что не ходят в БД.
Клавиатуры с данными юзера (habits_keyboard, settings_keyboard) остаются
в bot.py, потому что зависят от db_* функций.

Сюда же перенесена константа SPHERES — она используется почти везде только
для UI-подписей, и её логичнее держать рядом с клавиатурами сфер.
"""

from telegram import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# 10 сфер жизни — идентификаторы и русские подписи с эмодзи.
SPHERES = {
    "work":       "💼 Работа & Карьера",
    "finance":    "💰 Финансы & Деньги",
    "family":     "👨‍👩‍👧 Семья & Близкие",
    "relations":  "🤝 Отношения & Социум",
    "health":     "💛 Здоровье & Тело",
    "psychology": "🧠 Психология & Внутреннее",
    "growth":     "🌱 Развитие & Обучение",
    "energy":     "✨ Энергия & Духовность",
    "home":       "🏠 Быт & Пространство",
    "projects":   "🎯 Проекты & Идеи",
}
SPHERE_KEYS = list(SPHERES.keys())


def score_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Две строки по 5 кнопок: оценка от 1 до 10. prefix задаёт callback_data."""
    row1 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}_{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}_{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])


def main_keyboard() -> ReplyKeyboardMarkup:
    """Главное нижнее меню — показывается всегда после онбординга."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 Задачи"), KeyboardButton("🎯 Цели")],
        [KeyboardButton("🌀 Сферы жизни"), KeyboardButton("💡 Идеи")],
        [KeyboardButton("📊 Дашборд"), KeyboardButton("📅 План недели")],
    ], resize_keyboard=True)


def onboarding_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-кнопки под сообщениями онбординга. Позволяют выйти в любой момент
    или рассказать о себе больше."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Завершить знакомство", callback_data="finish_onboarding")],
        [InlineKeyboardButton("💡 Узнай меня больше",   callback_data="know_me_more")],
    ])


def tasks_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сегодня",  callback_data="tasks_today"),
         InlineKeyboardButton("📆 Неделя",   callback_data="tasks_week")],
        [InlineKeyboardButton("🗓 Месяц",    callback_data="tasks_month"),
         InlineKeyboardButton("♾ Долгосрочные", callback_data="tasks_longterm")],
        [InlineKeyboardButton("🔴 Срочные",  callback_data="tasks_urgent"),
         InlineKeyboardButton("✅ Выполненные", callback_data="tasks_done")],
        [InlineKeyboardButton("📋 Все",      callback_data="tasks_all"),
         InlineKeyboardButton("⬅️ Назад",    callback_data="back_main")],
    ])


def goals_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Краткосрочные", callback_data="goals_short"),
         InlineKeyboardButton("🏔 Долгосрочные",  callback_data="goals_long")],
        [InlineKeyboardButton("📋 Все цели",       callback_data="goals_all"),
         InlineKeyboardButton("⬅️ Назад",          callback_data="back_main")],
    ])


def spheres_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    items = list(SPHERES.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(label, callback_data=f"sphere_{key}")
               for key, label in items[i:i + 2]]
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)


def sphere_detail_keyboard(sphere_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Задачи", callback_data=f"sph_tasks_{sphere_key}"),
         InlineKeyboardButton("🎯 Цели",   callback_data=f"sph_goals_{sphere_key}")],
        [InlineKeyboardButton("💡 Идеи",   callback_data=f"sph_ideas_{sphere_key}"),
         InlineKeyboardButton("⬅️ К сферам", callback_data="back_spheres")],
    ])


def task_actions_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Выполнено", callback_data=f"tdone_{task_id}"),
         InlineKeyboardButton("🗑 Удалить",    callback_data=f"tdel_{task_id}")],
        [InlineKeyboardButton("📅 Перенести", callback_data=f"tmove_{task_id}"),
         InlineKeyboardButton("⬅️ Назад",     callback_data="tasks_all")],
    ])


def move_timeframe_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сегодня",   callback_data=f"tset_today_{task_id}"),
         InlineKeyboardButton("📆 Завтра",    callback_data=f"tset_tomorrow_{task_id}")],
        [InlineKeyboardButton("🗓 На неделю", callback_data=f"tset_week_{task_id}"),
         InlineKeyboardButton("🗓 На месяц",  callback_data=f"tset_month_{task_id}")],
        [InlineKeyboardButton("⬅️ Назад",     callback_data="tasks_all")],
    ])
