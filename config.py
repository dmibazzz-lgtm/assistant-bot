"""Конфигурация бота Нова: переменные окружения, тарифы, модели, лимиты.

Всё что настраивается через Railway Variables или меняется редко — здесь.
Если нужно поднять цену тарифа или добавить API-ключ — правь этот файл.
"""

import os

# ── Секреты и URL (переменные окружения Railway) ─────────────────────────────

TELEGRAM_TOKEN       = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY")
TURSO_URL            = os.environ.get("TURSO_URL")
TURSO_TOKEN          = os.environ.get("TURSO_TOKEN")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
WEBHOOK_URL          = os.environ.get("WEBHOOK_URL", "https://assistant-bot-production-6438.up.railway.app")
OPENROUTER_API_KEY   = os.environ.get("OPENROUTER_API_KEY")
MEM0_API_KEY         = os.environ.get("MEM0_API_KEY")

# ID владельца (твой Telegram user_id). Только он видит /admin, /backup,
# /test_morning, /test_evening. Узнать свой ID: напиши боту /myid.
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)

# Replicate API — для генерации изображений через FLUX.1 Schnell (~$0.003 за картинку).
# Ключ брать на replicate.com/account/api-tokens. Без ключа /draw вежливо откажет.
REPLICATE_API_TOKEN  = os.environ.get("REPLICATE_API_TOKEN")

# Google OAuth scope — нужен только для Google Calendar.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ── Feature flag: монетизация ────────────────────────────────────────────────
# False — лимиты отключены, /subscribe и Stars-платежи не регистрируются,
#         все юзеры получают полный доступ бесплатно.
# True  — включает тарифы, проверку лимитов и оплату через Telegram Stars.
# Можно выставить PAYMENTS_ENABLED=true в Railway без изменения кода.
PAYMENTS_ENABLED = os.environ.get("PAYMENTS_ENABLED", "false").lower() in ("1", "true", "yes", "on")

# ── Тарифы ────────────────────────────────────────────────────────────────────
# Цены в Telegram Stars (XTR). 1 Star ≈ 1.5-2 ₽.
#
# trial — 7 дней бесплатно при первом /start (автоматически).
# basic — полный набор инструментов структурирования жизни.
# pro   — без лимитов + приоритетный Sonnet + продвинутые AI-функции.
#
# ⚠️ Чтобы поменять цены/лимиты — правь этот словарь. Больше ничего не нужно.
PLANS = {
    "trial": {
        "title":           "Пробный",
        "price_stars":     0,
        "period_days":     7,
        "msg_daily":       50,
        "voice_daily":     10,
        "photo_daily":     10,
        "calendar":        True,
        "smart_model":     False,
        "premium_ai":      False,
        "description":     "7 дней бесплатного доступа ко всем базовым функциям.",
    },
    "basic": {
        "title":           "Базовый",
        "price_stars":     299,     # ~450-600 ₽/мес
        "period_days":     30,
        "msg_daily":       200,
        "voice_daily":     30,
        "photo_daily":     30,
        "calendar":        True,
        "smart_model":     False,
        "premium_ai":      False,
        "description":     (
            "Полное структурирование жизни: задачи, цели, Google Calendar, "
            "сферы жизни, дневник, трекеры настроения/энергии/привычек, графики, "
            "PDF-отчёты, голос, фото, распознавание чеков, долгая память."
        ),
    },
    "pro": {
        "title":           "Pro",
        "price_stars":     799,     # ~1200-1600 ₽/мес
        "period_days":     30,
        "msg_daily":       10**9,
        "voice_daily":     10**9,
        "photo_daily":     10**9,
        "calendar":        True,
        "smart_model":     True,
        "premium_ai":      True,
        "description":     (
            "Всё из Базового БЕЗ ЛИМИТОВ + приоритетная модель Sonnet + "
            "продвинутые AI-функции: генерация изображений, создание презентаций, "
            "расширенный контекст памяти, приоритетная поддержка."
        ),
    },
}

# После истечения подписки: юзер видит свои данные (/today, /goals),
# но не может общаться с Новой, пока не продлит.
PLAN_EXPIRED = "expired"

# ── Модели LLM ───────────────────────────────────────────────────────────────
# Умная модель — для всего что требует глубокого анализа, работы с тегами
# и извлечения структуры.
MODEL_SMART = "claude-sonnet-4-6"

# Быстрые/дешёвые для простых ответов. OpenRouter с fallback на Claude Haiku.
MODEL_FAST_OPENROUTER = "deepseek/deepseek-chat-v3-0324"
MODEL_FAST_CLAUDE     = "claude-haiku-4-5-20251001"

# Лимиты вывода по типу запроса. Средний ответ Новы 150-300 токенов, поэтому
# max_tokens=1000 везде был перебором и защищал в основном от зацикливания.
MAX_TOKENS_DEFAULT  = 700    # обычный диалог
MAX_TOKENS_ONBOARD  = 1000   # онбординг — бывают длинные ответы с разбором
MAX_TOKENS_NOTIF    = 500    # утро / вечер — короткие приветствия
MAX_TOKENS_REVIEW   = 900    # еженедельный и месячный разбор
MAX_TOKENS_VISION   = 800    # фото — описание + извлечение задач

# Ключевые слова, по которым роутер сразу переключается на умную модель.
# Всё что не про задачи/календарь/анализ — идёт в дешёвую DeepSeek.
SMART_KEYWORDS = {
    "цель", "цели", "анализ", "отчёт", "отчет", "сферы", "сфера",
    "конфликт", "психолог", "рефлекси", "онбординг", "еженедельн",
    "прогресс", "мечта", "мечты", "стратег", "глубок", "проблема",
    "тревог", "кризис", "смысл", "ценност", "мотивац",
    # Задачи и планирование — только Claude надёжно генерирует теги
    "задач", "запиш", "добавь", "добавить", "напомни", "запланир",
    "сделать", "сделай", "внеси", "поставь", "зафиксир", "отметь",
    "календар", "перенеси", "удали", "выполни", "завтра", "сегодня",
    "неделя", "неделе", "месяц", "дедлайн", "срок", "план",
}
