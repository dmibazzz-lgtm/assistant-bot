import os, sys, httpx, sqlite3, json, logging, re, base64, io, random
from datetime import datetime, timedelta, timezone
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
TURSO_URL = os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://assistant-bot-production-6438.up.railway.app")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Дорогие запросы — через Claude Sonnet
MODEL_SMART = "claude-sonnet-4-6"
# Дешёвые запросы — через OpenRouter DeepSeek V3 (или Haiku как fallback)
MODEL_FAST_OPENROUTER = "deepseek/deepseek-chat-v3-0324"
MODEL_FAST_CLAUDE     = "claude-haiku-4-5-20251001"

_SMART_KEYWORDS = {
    "цель", "цели", "анализ", "отчёт", "отчет", "сферы", "сфера",
    "конфликт", "психолог", "рефлекси", "онбординг", "еженедельн",
    "прогресс", "мечта", "мечты", "стратег", "глубок", "проблема",
    "тревог", "кризис", "смысл", "ценност", "мотивац",
    # Задачи и планирование — только Claude генерирует теги надёжно
    "задач", "запиш", "добавь", "добавить", "напомни", "запланир",
    "сделать", "сделай", "внеси", "поставь", "зафиксир", "отметь",
    "календар", "перенеси", "удали", "выполни", "завтра", "сегодня",
    "неделя", "неделе", "месяц", "дедлайн", "срок", "план",
}

def pick_model(messages):
    """Возвращает (model_id, provider) где provider = 'claude' | 'openrouter'"""
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if not isinstance(last, str):
        return MODEL_SMART, "claude"
    text = last.lower()
    if len(last) > 50 or any(kw in text for kw in _SMART_KEYWORDS):
        return MODEL_SMART, "claude"
    # Дешёвые запросы — DeepSeek через OpenRouter если ключ есть
    if OPENROUTER_API_KEY:
        return MODEL_FAST_OPENROUTER, "openrouter"
    return MODEL_FAST_CLAUDE, "claude"

QUOTES = [
    ("Единственный человек, которым ты должен стараться быть лучше, — это ты вчера.", "Аноним"),
    ("Не бойся расти медленно. Бойся стоять на месте.", "Китайская мудрость"),
    ("Успех — это сумма небольших усилий, повторяемых день за днём.", "Роберт Коллиер"),
    ("Каждый день — это новая возможность стать лучшей версией себя.", "Аноним"),
    ("Рост начинается там, где заканчивается зона комфорта.", "Нил Доналд Уолш"),
    ("Не ждите. Время никогда не будет подходящим.", "Наполеон Хилл"),
    ("Дисциплина — это мост между целями и достижениями.", "Джим Рон"),
    ("Маленький прогресс каждый день суммируется в большие результаты.", "Аноним"),
    ("Цель без плана — это просто мечта.", "Антуан де Сент-Экзюпери"),
    ("Ставь большие цели, потому что маленькие не воспламеняют сердца.", "Микеланджело"),
    ("Люди с ясными целями делают прогресс даже на труднейших дорогах.", "Томас Карлейль"),
    ("Секрет успеха: начни.", "Марк Твен"),
    ("Никогда не поздно быть тем, кем ты мог бы стать.", "Джордж Элиот"),
    ("Каждая большая цель была когда-то невозможной.", "Аноним"),
    ("Мечтай о большом, начинай с малого, действуй сейчас.", "Рой Беннетт"),
    ("Ты не можешь вернуться и изменить начало, но можешь начать сейчас и изменить конец.", "К.С. Льюис"),
    ("Успех обычно приходит к тем, кто слишком занят, чтобы его искать.", "Генри Дэвид Торо"),
    ("Присутствовать в моменте — это высшая форма благодарности.", "Тит Нат Хан"),
    ("Твоё время ограничено. Не трать его, живя чужой жизнью.", "Стив Джобс"),
    ("Всё, что ты ищешь снаружи, уже есть внутри тебя.", "Руми"),
    ("Пауза — это не потеря времени. Это инвестиция в ясность мышления.", "Аноним"),
    ("Осознанность — это замечать жизнь, а не просто проживать её.", "Аноним"),
    ("Когда ум спокоен, всё становится возможным.", "Аноним"),
    ("Не будь занят — будь продуктивен.", "Тим Феррис"),
    ("Фокус — это сказать «нет» сотне хороших идей ради одной великой.", "Стив Джобс"),
    ("Начни с самой неприятной задачи. Остаток дня будет победой.", "Брайан Трейси"),
    ("Прогресс, а не совершенство.", "Аноним"),
    ("Делай меньше, но лучше.", "Дитер Рамс"),
    ("Систематичность важнее вдохновения.", "Аноним"),
    ("Управляй энергией, не временем.", "Джим Лоэр"),
    ("Один хорошо выполненный час стоит десяти потраченных вхолостую.", "Аноним"),
    ("Жизнь — это не то, что с тобой происходит, а то, что ты из этого делаешь.", "Аноним"),
    ("Единственный способ делать отличную работу — любить то, что делаешь.", "Стив Джобс"),
    ("Ты достаточен. Прямо сейчас.", "Аноним"),
    ("Падение — часть роста. Подъём — твой выбор.", "Аноним"),
    ("Не сравнивай свою главу 1 с чьей-то главой 20.", "Аноним"),
    ("Трудности — это не препятствия на пути. Они и есть путь.", "Аноним"),
    ("Твоя реакция — твоя суперсила.", "Аноним"),
    ("Доверяй процессу. Результат придёт.", "Аноним"),
    ("Смелость — не отсутствие страха, а решение, что что-то важнее страха.", "Нельсон Мандела"),
    ("Будь таким человеком, которого ты сам хотел бы встретить.", "Аноним"),
    ("Окружи себя теми, кто тянет тебя вверх.", "Опра Уинфри"),
    ("Каждый человек в твоей жизни — учитель.", "Аноним"),
    ("Слушать — это тоже форма любви.", "Аноним"),
    ("Твоё тело — дом твоей жизни. Заботься о нём.", "Аноним"),
    ("Движение — это жизнь. Остановка — начало конца.", "Аноним"),
    ("Сон — не роскошь, а суперсила.", "Аноним"),
    ("Восстановление так же важно, как и работа.", "Аноним"),
    ("Маленькая забота о себе каждый день лучше большого ухода раз в год.", "Аноним"),
    ("Богатство — это свобода выбора, а не просто деньги.", "Аноним"),
    ("Инвестируй в себя. Это лучший вклад.", "Уоррен Баффет"),
    ("Прошлое — урок. Настоящее — дар. Будущее — возможность.", "Аноним"),
    ("Каждое утро — это второй шанс.", "Аноним"),
    ("Делай то, что считаешь правильным, даже когда никто не смотрит.", "Аноним"),
    ("Твоя история ещё пишется. Ты — автор.", "Аноним"),
    ("Достаточно одного маленького шага вперёд каждый день.", "Аноним"),
    ("Мудрость начинается с вопроса.", "Сократ"),
    ("Единственный провал — не попробовать.", "Аноним"),
    ("Стань тем изменением, которое хочешь видеть в мире.", "Махатма Ганди"),
    ("Верь в себя — и ты уже на полпути.", "Теодор Рузвельт"),
    ("Время уходит. Намерения остаются. Действуй сейчас.", "Аноним"),
    ("Твои мысли формируют твою реальность.", "Аноним"),
    ("Не бойся быть новичком. Все мастера когда-то им были.", "Аноним"),
    ("Настойчивость побеждает таланты, которые не работают.", "Аноним"),
    ("Хватит готовиться быть готовым. Начни.", "Аноним"),
    ("Качество твоих вопросов определяет качество твоей жизни.", "Аноним"),
    ("Делай что можешь, с тем что есть, там где ты есть.", "Теодор Рузвельт"),
    ("Лучший момент посадить дерево — 20 лет назад. Второй лучший — сейчас.", "Китайская мудрость"),
    ("Кто ясно мыслит — тот ясно действует.", "Аноним"),
    ("Совершенство — враг готового. Заверши хоть что-то.", "Вольтер"),
    ("Не жди вдохновения. Действие порождает вдохновение.", "Аноним"),
    ("Ты сильнее, чем думаешь.", "Аноним"),
    ("Смотри на проблему как на задачу — и она начнёт решаться.", "Аноним"),
    ("Гибкость ума важнее жёсткости планов.", "Аноним"),
    ("Привычки строят судьбу.", "Аноним"),
    ("Успех любит скорость и конкретность.", "Аноним"),
    ("Каждая трудность — тест на настоящие ценности.", "Аноним"),
    ("Самая продуктивная вещь — знать, что не делать.", "Питер Друкер"),
    ("Люби процесс — результат придёт сам.", "Аноним"),
    ("Ты не обязан чувствовать себя готовым. Просто начни.", "Аноним"),
    ("Сила не в том, чтобы не уставать, а в том, чтобы восстанавливаться.", "Аноним"),
    ("Где внимание — там энергия. Направляй осознанно.", "Аноним"),
    ("Честность с собой — начало любых перемен.", "Аноним"),
    ("Маленькое последовательное действие меняет всё.", "Аноним"),
    ("Лучшее инвестирование — в собственные навыки.", "Бенджамин Франклин"),
    ("Жизнь измеряется не годами, а моментами присутствия.", "Аноним"),
    ("Хаос снаружи начинается с хаоса внутри.", "Аноним"),
    ("Каждый день выбирай рост.", "Аноним"),
    ("Тот, кто движется медленно, но постоянно — приходит дальше.", "Аноним"),
    ("Доверяй себе больше, чем обстоятельствам.", "Аноним"),
    ("Перестань объяснять. Начни показывать результатами.", "Аноним"),
    ("Измени свои мысли — и ты изменишь мир.", "Норман Пил"),
    ("Выбор есть всегда. Даже ничего не делать — это выбор.", "Аноним"),
    ("Один шаг вперёд — уже прогресс.", "Аноним"),
    ("Всё великое начинается с малого и незаметного.", "Лао-Цзы"),
    ("Тот, кто контролирует своё внимание — контролирует свою жизнь.", "Аноним"),
    ("Страдание — необязательно. Рост — обязательно.", "Аноним"),
    ("Настоящий успех — жить по своим ценностям.", "Аноним"),
    ("Ошибка — это просто данные. Используй их.", "Аноним"),
    ("Ничто великое не было сделано без энтузиазма.", "Ральф Уолдо Эмерсон"),
    ("Сначала позаботься о себе. Потом ты сможешь позаботиться о других.", "Аноним"),
    ("Жизнь даётся один раз — проживи её максимально.", "Аноним"),
    ("В конце ты пожалеешь только о том, чего не сделал.", "Марк Твен"),
    ("Радость — не в обладании, а в движении к цели.", "Аноним"),
    ("Тяжело в учении — легко в бою.", "Александр Суворов"),
    ("Самопознание — начало всякой мудрости.", "Аристотель"),
]

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        try:
            import libsql_experimental as libsql
            conn = libsql.connect("nova.db", sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
            conn.sync()
            return conn
        except Exception as e:
            logging.warning(f"Turso failed: {e}")
    return sqlite3.connect("assistant.db")

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        onboarding_done INTEGER DEFAULT 0,
        onboarding_step INTEGER DEFAULT 0,
        profile TEXT DEFAULT '{}')""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, role TEXT, content TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        priority TEXT DEFAULT 'normal',
        sphere TEXT DEFAULT 'general',
        timeframe TEXT DEFAULT 'week',
        done INTEGER DEFAULT 0,
        due_date TEXT, created_at TEXT,
        done_at TEXT)""")
    try:
        c.execute("ALTER TABLE tasks ADD COLUMN done_at TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        sphere TEXT DEFAULT 'general',
        timeframe TEXT DEFAULT 'longterm',
        progress INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        sphere TEXT DEFAULT 'general',
        reviewed_at TEXT,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sphere_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, sphere TEXT, activity_date TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS google_tokens (
        user_id INTEGER PRIMARY KEY,
        token TEXT,
        refresh_token TEXT,
        token_uri TEXT,
        client_id TEXT,
        client_secret TEXT,
        scopes TEXT,
        expiry TEXT)""")
    try:
        c.execute("ALTER TABLE google_tokens ADD COLUMN expiry TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS sent_quotes (
        user_id INTEGER,
        quote_idx INTEGER,
        PRIMARY KEY (user_id, quote_idx))""")
    c.execute("""CREATE TABLE IF NOT EXISTS followup_queue (
        user_id INTEGER PRIMARY KEY,
        asked_at TEXT,
        attempts INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mood_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        note TEXT,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS energy_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS habits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS habit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        habit_id INTEGER,
        log_date TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        question TEXT,
        entry TEXT,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        created_at TEXT)""")
    conn.commit()
    if hasattr(conn, 'sync'): conn.sync()
    conn.close()

def db_exec(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, tuple(params))
    conn.commit()
    if hasattr(conn, 'sync'): conn.sync()
    conn.close()

def db_fetch(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()
    return rows

def db_fetchone(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, tuple(params))
    row = c.fetchone()
    conn.close()
    return row

def ensure_user(uid):
    db_exec("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))

def get_user(uid):
    return db_fetchone("SELECT * FROM users WHERE user_id=?", (uid,))

def update_user(uid, **kw):
    for k, v in kw.items():
        db_exec(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))

def get_profile(uid):
    row = db_fetchone("SELECT profile FROM users WHERE user_id=?", (uid,))
    return json.loads(row[0]) if row else {}

def save_profile(uid, profile):
    db_exec("UPDATE users SET profile=? WHERE user_id=?",
            (json.dumps(profile, ensure_ascii=False), uid))

def save_msg(uid, role, content):
    db_exec("INSERT INTO messages (user_id,role,content,created_at) VALUES (?,?,?,?)",
            (uid, role, content, datetime.now().isoformat()))

def get_history(uid, limit=20):
    rows = db_fetch("SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_history(uid):
    db_exec("DELETE FROM messages WHERE user_id=?", (uid,))

def add_task(uid, text, priority="normal", sphere="general", timeframe="week", due_date=None):
    existing = db_fetch("SELECT id FROM tasks WHERE user_id=? AND text=? AND done=0", (uid, text))
    if existing: return
    db_exec("INSERT INTO tasks (user_id,text,priority,sphere,timeframe,due_date,created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, text, priority, sphere, timeframe, due_date, datetime.now().isoformat()))

def get_tasks(uid, sphere=None, timeframe=None, priority=None, done=0):
    query = "SELECT id,text,priority,sphere,timeframe,due_date FROM tasks WHERE user_id=? AND done=?"
    params = [uid, done]
    if sphere: query += " AND sphere=?"; params.append(sphere)
    if timeframe: query += " AND timeframe=?"; params.append(timeframe)
    if priority: query += " AND priority=?"; params.append(priority)
    query += " ORDER BY id"
    return db_fetch(query, tuple(params))

def get_today_tasks(uid):
    today = datetime.now().date().isoformat()
    return db_fetch("""SELECT id,text,priority,sphere,timeframe,due_date FROM tasks
                 WHERE user_id=? AND done=0 AND (timeframe='today' OR due_date=?)
                 ORDER BY priority DESC""", (uid, today))

def complete_task(task_id):
    db_exec("UPDATE tasks SET done=1, done_at=? WHERE id=?",
            (datetime.now().isoformat(), task_id))

def delete_task(task_id):
    db_exec("DELETE FROM tasks WHERE id=?", (task_id,))

_UNSET = object()

def edit_task(task_id, text=None, priority=None, timeframe=None, due_date=_UNSET):
    if text: db_exec("UPDATE tasks SET text=? WHERE id=?", (text, task_id))
    if priority: db_exec("UPDATE tasks SET priority=? WHERE id=?", (priority, task_id))
    if timeframe: db_exec("UPDATE tasks SET timeframe=? WHERE id=?", (timeframe, task_id))
    if due_date is not _UNSET: db_exec("UPDATE tasks SET due_date=? WHERE id=?", (due_date, task_id))

def update_goal_progress(goal_id, progress):
    db_exec("UPDATE goals SET progress=? WHERE id=?", (progress, goal_id))

def get_user_tz_offset(profile):
    tz = profile.get("timezone", "")
    if not tz:
        return 0
    m = re.search(r'([+-]?\d+)', tz)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return 0

def user_now(profile):
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=get_user_tz_offset(profile))

REFLECT_QUESTIONS = [
    "Чего ты сейчас больше всего избегаешь — и почему?",
    "Если бы твой лучший друг описал тебя незнакомцу, что бы он сказал?",
    "Что ты делаешь из страха, а не из желания?",
    "Какое решение ты откладываешь уже давно?",
    "Что ты хотел бы простить себе?",
    "Назови три вещи, которые тебя по-настоящему заряжают.",
    "Что бы ты сделал, если бы знал что не провалишься?",
    "Какую версию себя ты строишь прямо сейчас?",
    "Что ты делаешь на автопилоте — и стоит ли это менять?",
    "Когда ты последний раз делал что-то впервые?",
    "Что ты хочешь, чтобы люди чувствовали рядом с тобой?",
    "Чем ты гордишься, но никому не говоришь?",
    "Что для тебя значит 'достаточно хорошо'?",
    "Какие убеждения о себе тебе мешают?",
    "Что ты перестал делать, но скучаешь по этому?",
    "Где ты живёшь не своей жизнью?",
    "Что ты готов защищать даже под давлением?",
    "Если через 5 лет оглянешься — о чём не пожалеешь?",
    "Что ты говоришь себе когда всё идёт не так?",
    "Кто ты без своих ролей — без работы, без статуса, без отношений?",
    "Что тебе даёт ощущение смысла?",
    "Когда ты последний раз был полностью честен с собой?",
    "Что ты хочешь создать в этом мире?",
    "Каким человеком хочет стать тот ты, которого ты сейчас строишь?",
    "Что ты не говоришь вслух, но думаешь постоянно?",
    "Назови одну привычку, которая тебя держит назад.",
    "Что значит для тебя успех — не чужой, а твой?",
    "Что ты чувствуешь, когда делаешь что-то для других?",
    "Где граница между заботой о себе и эгоизмом для тебя?",
    "Что ты изменил бы в прошлом — и изменил бы на самом деле?",
    "Как ты справляешься с неопределённостью?",
    "Что тебя вдохновляло в детстве — и живо ли это сейчас?",
    "Когда ты чувствуешь себя по-настоящему живым?",
    "Что ты принимаешь в себе с трудом?",
    "Где в твоей жизни больше всего хаоса — и почему?",
    "Что ты делаешь хорошо, но обесцениваешь?",
    "Какой ты в отношениях с людьми которые тебя не знают?",
    "Что тебя останавливает чаще всего?",
    "Как ты относишься к своим ошибкам?",
    "Что ты ищешь в других людях — и есть ли это в тебе самом?",
    "Если бы ты мог поговорить с собой 10-летней давности — что бы сказал?",
    "Что тебе нужно услышать прямо сейчас?",
    "Где ты застрял — и что не даёт сдвинуться?",
    "Что ты называешь 'реализмом', а на деле это страх?",
    "Когда ты последний раз просил о помощи?",
    "Что для тебя важнее — быть правым или быть счастливым?",
    "Назови три вещи, которые ты хочешь отпустить.",
    "Что ты будешь помнить об этом периоде жизни через 20 лет?",
    "Как ты относишься к своему телу — как к другу или врагу?",
    "Что ты не позволяешь себе хотеть?",
    "Где в твоей жизни есть несоответствие между словами и делами?",
    "Что ты делаешь когда никто не видит?",
    "Какой вопрос ты боишься себе задать?",
]

# ── Цитаты ────────────────────────────────────────────────────────────────────

def get_random_quote(uid):
    sent = {r[0] for r in db_fetch("SELECT quote_idx FROM sent_quotes WHERE user_id=?", (uid,))}
    available = [i for i in range(len(QUOTES)) if i not in sent]
    if not available:
        db_exec("DELETE FROM sent_quotes WHERE user_id=?", (uid,))
        available = list(range(len(QUOTES)))
    idx = random.choice(available)
    db_exec("INSERT OR IGNORE INTO sent_quotes (user_id, quote_idx) VALUES (?,?)", (uid, idx))
    return QUOTES[idx]

# ── Follow-up ─────────────────────────────────────────────────────────────────

def set_followup(uid):
    db_exec("INSERT OR REPLACE INTO followup_queue (user_id, asked_at, attempts) VALUES (?,?,0)",
            (uid, datetime.now().isoformat()))

def clear_followup(uid):
    db_exec("DELETE FROM followup_queue WHERE user_id=?", (uid,))

def get_pending_followups():
    threshold = (datetime.now() - timedelta(hours=1)).isoformat()
    return db_fetch("SELECT user_id, asked_at, attempts FROM followup_queue WHERE asked_at < ? AND attempts < 2",
                    (threshold,))

# ── Визуальные отчёты ─────────────────────────────────────────────────────────

def generate_sphere_chart(uid):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        stats = get_sphere_stats(uid)
        goals = get_goals(uid)

        has_stats = bool(stats)
        has_goals = bool(goals)
        if not has_stats and not has_goals:
            return None

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        fig.patch.set_facecolor('#1a1a2e')
        for ax in axes:
            ax.set_facecolor('#16213e')
            ax.tick_params(colors='#e0e0e0')
            ax.spines[:].set_color('#444')

        if has_stats:
            labels = [SPHERES.get(k, k).split()[-1] for k in stats.keys()]
            values = list(stats.values())
            bars = axes[0].barh(labels, values, color='#6C63FF', height=0.6)
            axes[0].set_title('Активность по сферам (7 дней)', color='#e0e0e0', pad=10)
            axes[0].set_xlabel('дней', color='#aaa')
            for bar, val in zip(bars, values):
                axes[0].text(bar.get_width() + 0.05, bar.get_y() + bar.get_height() / 2,
                             str(val), va='center', color='#e0e0e0', fontsize=9)
        else:
            axes[0].text(0.5, 0.5, 'Нет данных', ha='center', va='center',
                         color='#888', transform=axes[0].transAxes)
            axes[0].set_title('Активность по сферам', color='#e0e0e0')

        if has_goals:
            goal_names = [(g[1][:18] + '…' if len(g[1]) > 18 else g[1]) for g in goals[:6]]
            goal_vals = [g[4] for g in goals[:6]]
            colors = ['#FF6584' if v < 30 else '#FFCA3A' if v < 70 else '#6BCB77' for v in goal_vals]
            axes[1].barh(goal_names, goal_vals, color=colors, height=0.6)
            axes[1].set_xlim(0, 100)
            axes[1].set_title('Прогресс целей (%)', color='#e0e0e0', pad=10)
            axes[1].set_xlabel('%', color='#aaa')
            for i, val in enumerate(goal_vals):
                axes[1].text(val + 1, i, f'{val}%', va='center', color='#e0e0e0', fontsize=9)
        else:
            axes[1].text(0.5, 0.5, 'Целей нет', ha='center', va='center',
                         color='#888', transform=axes[1].transAxes)
            axes[1].set_title('Прогресс целей', color='#e0e0e0')

        plt.tight_layout(pad=2)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=110, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        logging.error(f"Chart error: {e}")
        return None

def generate_pdf_report(uid):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib import colors

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=40, leftMargin=40,
                                topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=colors.HexColor('#6C63FF'))
        normal = styles['Normal']
        story = []

        profile = get_profile(uid)
        name = profile.get("name", "Пользователь")
        now = datetime.now()

        story.append(Paragraph(f"Отчёт Nova — {name}", styles['Title']))
        story.append(Paragraph(now.strftime('%d.%m.%Y'), normal))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#6C63FF')))
        story.append(Spacer(1, 12))

        tasks = get_tasks(uid)
        story.append(Paragraph("📋 Открытые задачи", h2))
        if tasks:
            for t in tasks[:30]:
                icon = {'urgent': '🔴', 'important': '🟡'}.get(t[2], '⚪')
                story.append(Paragraph(f"{icon} {t[1]}", normal))
        else:
            story.append(Paragraph("Задач нет.", normal))
        story.append(Spacer(1, 12))

        goals = get_goals(uid)
        story.append(Paragraph("🎯 Активные цели", h2))
        if goals:
            for g in goals:
                progress = g[4] if len(g) > 4 else 0
                bar = '█' * (progress // 10) + '░' * (10 - progress // 10)
                story.append(Paragraph(f"• {g[1]}  [{bar}] {progress}%", normal))
        else:
            story.append(Paragraph("Целей нет.", normal))
        story.append(Spacer(1, 12))

        ideas = get_ideas(uid)
        story.append(Paragraph("💡 Идеи", h2))
        if ideas:
            for i in ideas[:20]:
                story.append(Paragraph(f"• {i[1]}", normal))
        else:
            story.append(Paragraph("Идей нет.", normal))

        doc.build(story)
        buf.seek(0)
        return buf
    except Exception as e:
        logging.error(f"PDF error: {e}")
        return None

# ── Mood / Energy / Habits / Journal / Wins ───────────────────────────────────

def log_mood(uid, score, note=""):
    db_exec("INSERT INTO mood_log (user_id,score,note,created_at) VALUES (?,?,?,?)",
            (uid, score, note, datetime.now().isoformat()))

def get_mood_history(uid, days=14):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return db_fetch("SELECT score,note,created_at FROM mood_log WHERE user_id=? AND created_at>=? ORDER BY created_at",
                    (uid, cutoff))

def log_energy(uid, score):
    db_exec("INSERT INTO energy_log (user_id,score,created_at) VALUES (?,?,?)",
            (uid, score, datetime.now().isoformat()))

def get_energy_history(uid, days=14):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return db_fetch("SELECT score,created_at FROM energy_log WHERE user_id=? AND created_at>=? ORDER BY created_at",
                    (uid, cutoff))

def get_or_create_habit(uid, name):
    existing = db_fetchone("SELECT id FROM habits WHERE user_id=? AND name=? AND active=1", (uid, name))
    if existing: return existing[0]
    db_exec("INSERT INTO habits (user_id,name,active,created_at) VALUES (?,?,1,?)",
            (uid, name, datetime.now().isoformat()))
    return db_fetchone("SELECT id FROM habits WHERE user_id=? AND name=? AND active=1", (uid, name))[0]

def get_habits(uid):
    return db_fetch("SELECT id,name FROM habits WHERE user_id=? AND active=1 ORDER BY id", (uid,))

def mark_habit_today(uid, habit_id):
    today = datetime.now().date().isoformat()
    existing = db_fetchone("SELECT id FROM habit_log WHERE user_id=? AND habit_id=? AND log_date=?",
                           (uid, habit_id, today))
    if not existing:
        db_exec("INSERT INTO habit_log (user_id,habit_id,log_date) VALUES (?,?,?)", (uid, habit_id, today))

def get_habit_streak(uid, habit_id):
    rows = db_fetch("SELECT log_date FROM habit_log WHERE user_id=? AND habit_id=? ORDER BY log_date DESC",
                    (uid, habit_id))
    if not rows: return 0
    streak = 0
    check = datetime.now().date()
    for (d,) in rows:
        if d == check.isoformat():
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak

def save_journal(uid, question, entry):
    db_exec("INSERT INTO journal (user_id,question,entry,created_at) VALUES (?,?,?,?)",
            (uid, question, entry, datetime.now().isoformat()))

def get_journal_entries(uid, limit=5):
    return db_fetch("SELECT question,entry,created_at FROM journal WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (uid, limit))

def add_win(uid, text):
    db_exec("INSERT INTO wins (user_id,text,created_at) VALUES (?,?,?)",
            (uid, text, datetime.now().isoformat()))

def get_wins(uid, limit=10):
    return db_fetch("SELECT text,created_at FROM wins WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (uid, limit))

# ── Колесо жизни ──────────────────────────────────────────────────────────────

def generate_wheel_chart(uid):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np

        profile = get_profile(uid)
        spheres_score_str = profile.get("spheres_score", "")
        scores = {}
        if spheres_score_str:
            for pair in spheres_score_str.split(","):
                if ":" in pair:
                    k, v = pair.strip().split(":", 1)
                    try: scores[k.strip()] = int(v.strip())
                    except: pass

        labels_map = {
            "работа": "Работа", "финансы": "Финансы", "здоровье": "Здоровье",
            "отношения": "Отношения", "семья": "Семья", "саморазвитие": "Развитие",
            "творчество": "Творчество", "отдых": "Отдых",
            "духовность": "Духовность", "окружение": "Окружение",
        }
        keys = list(labels_map.keys())
        values = [scores.get(k, 5) for k in keys]
        labels = [labels_map[k] for k in keys]
        N = len(labels)
        angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
        values_plot = values + [values[0]]
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#16213e')
        ax.plot(angles, values_plot, 'o-', linewidth=2, color='#6C63FF')
        ax.fill(angles, values_plot, alpha=0.25, color='#6C63FF')
        ax.set_ylim(0, 10)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, color='#e0e0e0', fontsize=9)
        ax.tick_params(colors='#aaa')
        ax.yaxis.set_tick_params(labelcolor='#666')
        ax.spines['polar'].set_color('#444')
        ax.set_title('Колесо жизни', color='#e0e0e0', pad=15)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=110, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        logging.error(f"Wheel chart error: {e}")
        return None

# ── Инлайн-клавиатуры для трекеров ───────────────────────────────────────────

def score_keyboard(prefix):
    row1 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}_{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}_{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])

def habits_keyboard(uid):
    habits = get_habits(uid)
    today = datetime.now().date().isoformat()
    rows = []
    for hid, hname in habits:
        done = db_fetchone("SELECT id FROM habit_log WHERE user_id=? AND habit_id=? AND log_date=?",
                           (uid, hid, today))
        mark = "✅" if done else "⬜"
        streak = get_habit_streak(uid, hid)
        label = f"{mark} {hname}" + (f" 🔥{streak}" if streak > 1 else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"habit_toggle_{hid}")])
    rows.append([InlineKeyboardButton("➕ Добавить привычку", callback_data="habit_add")])
    return InlineKeyboardMarkup(rows)

def settings_keyboard(profile):
    notif_morning = profile.get("notif_morning", "1") != "0"
    notif_evening = profile.get("notif_evening", "1") != "0"
    notif_weekly  = profile.get("notif_weekly",  "1") != "0"
    style = profile.get("info_style", "detailed")
    feedback = profile.get("feedback_style", "soft")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'🔔' if notif_morning else '🔕'} Утро", callback_data="set_morning"),
         InlineKeyboardButton(f"{'🔔' if notif_evening else '🔕'} Вечер", callback_data="set_evening"),
         InlineKeyboardButton(f"{'🔔' if notif_weekly  else '🔕'} Неделя", callback_data="set_weekly")],
        [InlineKeyboardButton(f"Стиль: {'кратко' if style=='brief' else 'подробно'}", callback_data="set_style"),
         InlineKeyboardButton(f"Тон: {'прямой' if feedback=='direct' else 'мягкий'}", callback_data="set_feedback")],
        [InlineKeyboardButton("🕐 Изменить часовой пояс", callback_data="set_tz")],
    ])

def add_goal(uid, text, sphere="general", timeframe="longterm"):
    existing = db_fetch("SELECT id FROM goals WHERE user_id=? AND text=? AND done=0", (uid, text))
    if existing: return
    db_exec("INSERT INTO goals (user_id,text,sphere,timeframe,created_at) VALUES (?,?,?,?,?)",
            (uid, text, sphere, timeframe, datetime.now().isoformat()))

def get_goals(uid, sphere=None, timeframe=None):
    query = "SELECT id,text,sphere,timeframe,progress FROM goals WHERE user_id=? AND done=0"
    params = [uid]
    if sphere: query += " AND sphere=?"; params.append(sphere)
    if timeframe: query += " AND timeframe=?"; params.append(timeframe)
    return db_fetch(query, tuple(params))

def add_idea(uid, text, sphere="general"):
    existing = db_fetch("SELECT id FROM ideas WHERE user_id=? AND text=?", (uid, text))
    if existing: return
    db_exec("INSERT INTO ideas (user_id,text,sphere,created_at) VALUES (?,?,?,?)",
            (uid, text, sphere, datetime.now().isoformat()))

def get_ideas(uid, sphere=None):
    if sphere:
        return db_fetch("SELECT id,text,sphere,created_at FROM ideas WHERE user_id=? AND sphere=?", (uid, sphere))
    return db_fetch("SELECT id,text,sphere,created_at FROM ideas WHERE user_id=?", (uid,))

def get_frozen_items(uid):
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()[:10]
    ideas = db_fetch("SELECT id,text,sphere,'idea' FROM ideas WHERE user_id=? AND (reviewed_at IS NULL OR reviewed_at < ?)", (uid, week_ago))
    goals = db_fetch("SELECT id,text,sphere,'goal' FROM goals WHERE user_id=? AND done=0 AND progress=0 AND created_at < ?", (uid, week_ago))
    return ideas[:3] + goals[:2]

def log_sphere_activity(uid, sphere):
    db_exec("INSERT INTO sphere_activity (user_id,sphere,activity_date) VALUES (?,?,?)",
            (uid, sphere, datetime.now().date().isoformat()))

def get_sphere_stats(uid):
    rows = db_fetch("""SELECT sphere, COUNT(*) FROM sphere_activity
                 WHERE user_id=? AND activity_date >= date('now', '-7 days')
                 GROUP BY sphere ORDER BY COUNT(*) DESC""", (uid,))
    return {r[0]: r[1] for r in rows}

def save_google_token(uid, creds):
    expiry_str = creds.expiry.isoformat() if creds.expiry else None
    db_exec("""INSERT OR REPLACE INTO google_tokens
               (user_id, token, refresh_token, token_uri, client_id, client_secret, scopes, expiry)
               VALUES (?,?,?,?,?,?,?,?)""",
            (uid, creds.token, creds.refresh_token, creds.token_uri,
             creds.client_id, creds.client_secret, json.dumps(list(creds.scopes)), expiry_str))

def get_google_token(uid):
    try:
        # Пробуем с колонкой expiry (новая схема)
        row = db_fetchone("SELECT user_id,token,refresh_token,token_uri,client_id,client_secret,scopes,expiry FROM google_tokens WHERE user_id=?", (uid,))
        if not row: return None
        from datetime import datetime as dt
        expiry = dt.fromisoformat(row[7]) if (len(row) > 7 and row[7]) else None
        creds = Credentials(
            token=row[1], refresh_token=row[2], token_uri=row[3],
            client_id=row[4], client_secret=row[5],
            scopes=json.loads(row[6]), expiry=expiry)
        return creds
    except Exception:
        try:
            # Fallback без expiry (старая схема)
            row = db_fetchone("SELECT user_id,token,refresh_token,token_uri,client_id,client_secret,scopes FROM google_tokens WHERE user_id=?", (uid,))
            if not row: return None
            creds = Credentials(
                token=row[1], refresh_token=row[2], token_uri=row[3],
                client_id=row[4], client_secret=row[5],
                scopes=json.loads(row[6]))
            return creds
        except Exception as e:
            logging.error(f"get_google_token error uid={uid}: {e}")
            return None

async def get_calendar_service(uid):
    import asyncio
    creds = get_google_token(uid)
    if not creds: return None
    try:
        loop = asyncio.get_running_loop()
        # Обновляем токен если истёк или нет информации о сроке (на всякий случай)
        if creds.refresh_token and (creds.expired or creds.expiry is None):
            from google.auth.transport.requests import Request
            await loop.run_in_executor(None, lambda: creds.refresh(Request()))
            save_google_token(uid, creds)
        service = await loop.run_in_executor(
            None, lambda: build("calendar", "v3", credentials=creds,
                                cache_discovery=False))
        return service
    except Exception as e:
        logging.error(f"Calendar service error for uid={uid}: {e}")
        return None

async def add_to_calendar(uid, task_text, due_date=None, timeframe=None, event_time=None):
    import asyncio
    service = await get_calendar_service(uid)
    if not service:
        logging.warning(f"Calendar service unavailable for user {uid}")
        return None
    try:
        now = datetime.now()
        if due_date:
            start_date = due_date
        elif timeframe == "today":
            start_date = now.date().isoformat()
        elif timeframe == "tomorrow":
            start_date = (now + timedelta(days=1)).date().isoformat()
        elif timeframe == "week":
            start_date = (now + timedelta(days=3)).date().isoformat()
        elif timeframe == "month":
            start_date = (now + timedelta(days=14)).date().isoformat()
        else:
            start_date = (now + timedelta(days=7)).date().isoformat()

        months = ["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"]
        from datetime import date as date_type
        d = date_type.fromisoformat(start_date)
        date_label = f"{d.day} {months[d.month-1]}"

        if event_time:
            # Событие с конкретным временем
            import pytz
            tz_str = get_profile(uid).get("timezone", "Europe/Moscow")
            try:
                tz = pytz.timezone(tz_str)
            except Exception:
                tz = pytz.timezone("Europe/Moscow")
            h, m = (event_time.split(":") + ["00"])[:2]
            start_dt = datetime.fromisoformat(start_date).replace(hour=int(h), minute=int(m))
            end_dt = start_dt + timedelta(hours=1)
            event = {
                "summary": task_text,
                "start": {"dateTime": tz.localize(start_dt).isoformat(), "timeZone": tz_str},
                "end":   {"dateTime": tz.localize(end_dt).isoformat(),   "timeZone": tz_str},
            }
            date_label = f"{d.day} {months[d.month-1]} в {h}:{m.zfill(2)}"
        else:
            event = {
                "summary": task_text,
                "start": {"date": start_date},
                "end":   {"date": start_date},
            }

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: service.events().insert(calendarId="primary", body=event).execute()
        )
        logging.info(f"Calendar event added for user {uid}: {task_text} @ {start_date}")
        return date_label
    except Exception as e:
        logging.error(f"Calendar add error for user {uid}: {e}")
        return None

async def list_calendar_events(uid, max_results=30, include_past=False):
    """Возвращает список событий [{id, summary, start, calendar_id}]"""
    import asyncio
    service = await get_calendar_service(uid)
    if not service: return []
    try:
        loop = asyncio.get_running_loop()
        kwargs = dict(calendarId="primary", maxResults=max_results,
                      singleEvents=True, orderBy="startTime")
        if not include_past:
            kwargs["timeMin"] = datetime.utcnow().isoformat() + "Z"
        result = await loop.run_in_executor(None, lambda: service.events().list(**kwargs).execute())
        items = result.get("items", [])
        events = []
        for e in items:
            start = e.get("start", {})
            events.append({
                "id": e["id"],
                "summary": e.get("summary", "(без названия)"),
                "start": start.get("dateTime", start.get("date", "")),
            })
        return events
    except Exception as e:
        logging.error(f"Calendar list error uid={uid}: {e}")
        return []

async def delete_calendar_event(uid, event_id):
    import asyncio
    service = await get_calendar_service(uid)
    if not service: return False
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: service.events().delete(
            calendarId="primary", eventId=event_id).execute())
        return True
    except Exception as e:
        logging.error(f"Calendar delete error uid={uid} event={event_id}: {e}")
        return False

async def update_calendar_event(uid, event_id, new_summary=None, new_date=None, new_time=None):
    import asyncio, pytz
    service = await get_calendar_service(uid)
    if not service: return False
    try:
        loop = asyncio.get_running_loop()
        event = await loop.run_in_executor(None, lambda: service.events().get(
            calendarId="primary", eventId=event_id).execute())
        if new_summary:
            event["summary"] = new_summary
        if new_date:
            tz_str = get_profile(uid).get("timezone", "Europe/Moscow")
            if new_time:
                try:
                    tz = pytz.timezone(tz_str)
                except Exception:
                    tz = pytz.timezone("Europe/Moscow")
                h, m = (new_time.split(":") + ["00"])[:2]
                start_dt = datetime.fromisoformat(new_date).replace(hour=int(h), minute=int(m))
                end_dt = start_dt + timedelta(hours=1)
                event["start"] = {"dateTime": tz.localize(start_dt).isoformat(), "timeZone": tz_str}
                event["end"]   = {"dateTime": tz.localize(end_dt).isoformat(),   "timeZone": tz_str}
            else:
                event["start"] = {"date": new_date}
                event["end"]   = {"date": new_date}
        await loop.run_in_executor(None, lambda: service.events().update(
            calendarId="primary", eventId=event_id, body=event).execute())
        return True
    except Exception as e:
        logging.error(f"Calendar update error uid={uid} event={event_id}: {e}")
        return False

async def list_calendars(uid):
    """Возвращает список всех календарей пользователя [{id, summary, primary}]"""
    import asyncio
    service = await get_calendar_service(uid)
    if not service: return []
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: service.calendarList().list().execute())
        cals = []
        for c in result.get("items", []):
            cals.append({
                "id": c["id"],
                "summary": c.get("summary", "(без названия)"),
                "primary": c.get("primary", False),
            })
        return cals
    except Exception as e:
        logging.error(f"Calendar list error uid={uid}: {e}")
        return []

async def delete_extra_calendar(uid, calendar_id):
    """Удаляет дополнительный календарь (не primary)"""
    import asyncio
    if calendar_id == "primary":
        return False
    service = await get_calendar_service(uid)
    if not service: return False
    try:
        loop = asyncio.get_running_loop()
        # Пробуем удалить, если нет прав — просто скрываем из списка
        try:
            await loop.run_in_executor(None, lambda: service.calendars().delete(calendarId=calendar_id).execute())
        except Exception:
            await loop.run_in_executor(None, lambda: service.calendarList().delete(calendarId=calendar_id).execute())
        return True
    except Exception as e:
        logging.error(f"Calendar delete error uid={uid} cal={calendar_id}: {e}")
        return False

async def clear_all_calendar_events(uid):
    """Удаляет ВСЕ события (прошлые и будущие) из основного календаря"""
    events = await list_calendar_events(uid, max_results=500, include_past=True)
    deleted = 0
    for e in events:
        ok = await delete_calendar_event(uid, e["id"])
        if ok:
            deleted += 1
    return deleted

def get_oauth_flow():
    import os
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{WEBHOOK_URL}/oauth/callback"]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=f"{WEBHOOK_URL}/oauth/callback"
    )
    return flow

SPHERES = {
    "work": "💼 Работа & Карьера",
    "finance": "💰 Финансы & Деньги",
    "family": "👨‍👩‍👧 Семья & Близкие",
    "relations": "🤝 Отношения & Социум",
    "health": "💛 Здоровье & Тело",
    "psychology": "🧠 Психология & Внутреннее",
    "growth": "🌱 Развитие & Обучение",
    "energy": "✨ Энергия & Духовность",
    "home": "🏠 Быт & Пространство",
    "projects": "🎯 Проекты & Идеи",
}
SPHERE_KEYS = list(SPHERES.keys())

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 Задачи"), KeyboardButton("🎯 Цели")],
        [KeyboardButton("🌀 Сферы жизни"), KeyboardButton("💡 Идеи")],
        [KeyboardButton("📊 Дашборд"), KeyboardButton("📅 План недели")]
    ], resize_keyboard=True)

def tasks_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сегодня", callback_data="tasks_today"),
         InlineKeyboardButton("📆 Неделя", callback_data="tasks_week")],
        [InlineKeyboardButton("🗓 Месяц", callback_data="tasks_month"),
         InlineKeyboardButton("♾ Долгосрочные", callback_data="tasks_longterm")],
        [InlineKeyboardButton("🔴 Срочные", callback_data="tasks_urgent"),
         InlineKeyboardButton("✅ Выполненные", callback_data="tasks_done")],
        [InlineKeyboardButton("📋 Все", callback_data="tasks_all"),
         InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
    ])

def goals_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Краткосрочные", callback_data="goals_short"),
         InlineKeyboardButton("🏔 Долгосрочные", callback_data="goals_long")],
        [InlineKeyboardButton("📋 Все цели", callback_data="goals_all"),
         InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
    ])

def spheres_keyboard():
    buttons = []
    items = list(SPHERES.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(label, callback_data=f"sphere_{key}")
               for key, label in items[i:i+2]]
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def sphere_detail_keyboard(sphere_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Задачи", callback_data=f"sph_tasks_{sphere_key}"),
         InlineKeyboardButton("🎯 Цели", callback_data=f"sph_goals_{sphere_key}")],
        [InlineKeyboardButton("💡 Идеи", callback_data=f"sph_ideas_{sphere_key}"),
         InlineKeyboardButton("⬅️ К сферам", callback_data="back_spheres")]
    ])

def task_actions_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Выполнено", callback_data=f"tdone_{task_id}"),
         InlineKeyboardButton("🗑 Удалить", callback_data=f"tdel_{task_id}")],
        [InlineKeyboardButton("📅 Перенести", callback_data=f"tmove_{task_id}"),
         InlineKeyboardButton("⬅️ Назад", callback_data="tasks_all")]
    ])

def move_timeframe_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сегодня", callback_data=f"tset_today_{task_id}"),
         InlineKeyboardButton("📆 Завтра", callback_data=f"tset_tomorrow_{task_id}")],
        [InlineKeyboardButton("🗓 На неделю", callback_data=f"tset_week_{task_id}"),
         InlineKeyboardButton("🗓 На месяц", callback_data=f"tset_month_{task_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="tasks_all")]
    ])

def format_tasks(tasks, with_actions=False):
    if not tasks: return "Пусто 👌"
    icons = {"urgent": "🔴", "important": "🟡", "normal": "⚪"}
    lines = []
    for t in tasks:
        icon = icons.get(t[2], "⚪")
        lines.append(f"{icon} [{t[0]}] {t[1]}")
    return "\n".join(lines)

def format_goals(goals):
    if not goals: return "Пусто 👌"
    lines = []
    for g in goals:
        progress = g[4] if len(g) > 4 else 0
        prog_str = f" — {progress}%" if progress else ""
        lines.append(f"• [{g[0]}] {g[1]}{prog_str}")
    return "\n".join(lines)

def format_week_plan(uid):
    now = datetime.now()
    lines = [f"📅 *План на неделю*\n{now.strftime('%d.%m')} — {(now + timedelta(days=6)).strftime('%d.%m')}\n"]
    today = get_today_tasks(uid)
    week = get_tasks(uid, timeframe="week")
    urgent = get_tasks(uid, priority="urgent")
    if urgent:
        lines.append("🔴 *Срочно:*")
        for t in urgent[:5]: lines.append(f"• {t[1]}")
        lines.append("")
    if today:
        lines.append("📅 *Сегодня:*")
        for t in today[:5]: lines.append(f"• {t[1]}")
        lines.append("")
    if week:
        lines.append("📆 *На этой неделе:*")
        for t in week[:7]: lines.append(f"• {t[1]}")
    if not urgent and not today and not week:
        lines.append("Задач нет — отличное время добавить новые 🙂")
    return "\n".join(lines)

def format_dashboard(uid):
    profile = get_profile(uid)
    name = profile.get("name", "")
    today_tasks = get_today_tasks(uid)
    all_tasks = get_tasks(uid)
    goals = get_goals(uid)
    ideas = get_ideas(uid)
    stats = get_sphere_stats(uid)
    urgent = [t for t in all_tasks if t[2] == "urgent"]
    now = datetime.now()
    gcal = get_google_token(uid)
    cal_status = "✅ подключён" if gcal else "❌ не подключён"
    lines = [
        f"📊 {name} — {now.strftime('%d.%m.%Y')}",
        "",
        f"📅 Сегодня: {len(today_tasks)} задач",
        f"📌 Всего: {len(all_tasks)} | 🔴 Срочных: {len(urgent)}",
        f"🎯 Целей: {len(goals)} | 💡 Идей: {len(ideas)}",
        f"📆 Google Календарь: {cal_status}",
    ]
    if stats:
        lines.append("")
        lines.append("Активность за 7 дней:")
        inactive = set(SPHERE_KEYS) - set(stats.keys())
        for sk, cnt in stats.items():
            lines.append(f"  {SPHERES.get(sk, sk)}: {'▓' * min(cnt, 8)}")
        if inactive:
            lines.append("😴 Без внимания:")
            for s in list(inactive)[:2]:
                lines.append(f"  {SPHERES.get(s, s)}")
    return "\n".join(lines)

ONBOARDING_INTRO = """Привет! Я Нова — твой личный ассистент нового поколения 😎
Предлагаю на ты, но скажи как тебе комфортнее.
Я здесь чтобы помогать вести дела, двигаться к целям и развиваться 👍
Чем лучше тебя узнаю — тем точнее помогу.
Онбординг займёт время, но это инвестиция. Если не хочешь — скажи, сразу перейдём к делам.

Вот что нас ждёт:
📍 Этап 1 — Знакомство
📍 Этап 2 — Как ты устроен
📍 Этап 3 — Сферы жизни
📍 Этап 4 — Всё что есть прямо сейчас
📍 Этап 5 — Цели, мечты, идеи

Поехали — узнаем друг друга! 🚀"""

def build_system(profile, onboarding_mode=False, uid=None, cal_events=None):
    address = profile.get("address") or profile.get("name") or ""
    p_lines = []
    if address: p_lines.append(f"Обращение: {address}")
    if profile.get("occupation"): p_lines.append(f"Работа/деятельность: {profile['occupation']}")
    if profile.get("goals"): p_lines.append(f"Жизненные цели: {profile['goals']}")
    if profile.get("pain"): p_lines.append(f"Что мешает: {profile['pain']}")
    if profile.get("satisfied"): p_lines.append(f"Что хорошо в жизни: {profile['satisfied']}")
    if profile.get("day_rhythm"): p_lines.append(f"Ритм дня: {profile['day_rhythm']}")
    if profile.get("timezone"): p_lines.append(f"Часовой пояс: {profile['timezone']}")
    if profile.get("character"): p_lines.append(f"Характер/особенности: {profile['character']}")
    if profile.get("notif_extras"): p_lines.append(f"Доп. блоки в уведомлениях: {profile['notif_extras']}")
    if profile.get("notes"): p_lines.append(f"Заметки: {profile['notes']}")
    profile_block = "\n".join(p_lines)

    now = user_now(profile)
    current_time = f"Сейчас: {now.strftime('%A, %d.%m.%Y, %H:%M')}"

    # Блок с текущими событиями для управления календарём
    if cal_events:
        ev_lines = ["Текущие события в основном календаре:"]
        cals_list = None
        for e in cal_events:
            if "_calendars" in e:
                cals_list = e["_calendars"]
            else:
                ev_lines.append("• id=" + e['id'] + " | " + e['summary'] + " | " + e['start'])
        if cals_list:
            ev_lines.append("\nДополнительные календари (папки):")
            for c in cals_list:
                marker = "(основной)" if c.get("primary") else ""
                ev_lines.append("• id=" + c['id'] + " | " + c['summary'] + " " + marker)
        cal_events_block = "\n".join(ev_lines)
    else:
        cal_events_block = "(события не загружены)"

    # Блок Calendar для системного промпта (без вложенных f-строк)
    if uid and get_google_token(uid):
        cal_block = (
            "✅ Подключён. Ты имеешь ПОЛНЫЙ доступ к Google Календарю пользователя.\n\n"
            "ДОБАВИТЬ событие → [TASK: название | приоритет | сфера | timeframe | HH:MM]\n"
            "УДАЛИТЬ ВСЕ события (прошлые и будущие) → [CAL_DELETE_ALL]\n"
            "УДАЛИТЬ одно событие → [CAL_DELETE: event_id]\n"
            "ИЗМЕНИТЬ событие → [CAL_UPDATE: event_id | новое название | YYYY-MM-DD | HH:MM]\n"
            "УДАЛИТЬ дополнительный календарь → [CAL_DELETE_CALENDAR: calendar_id]\n\n"
            "Когда пользователь просит:\n"
            '- "добавь задачу/событие" → используй [TASK: ...]\n'
            '- "удали всё / очисти" → [CAL_DELETE_ALL] — удалит ВСЕ события включая прошлые\n'
            '- "удали событие X" → [CAL_DELETE: id]\n'
            '- "удали папку/календарь X" → [CAL_DELETE_CALENDAR: id]\n'
            '- "перенеси / измени событие" → [CAL_UPDATE: id | ...]\n'
            '- "покажи что есть" → ответь на основе списков ниже\n\n'
            + cal_events_block
        )
    else:
        cal_block = "❌ Не подключён. Пользователь может подключить через /calendar. Не говори что у тебя нет интеграции с календарём — она есть, просто ещё не настроена."

    onboarding_block = ""
    if onboarding_mode:
        onboarding_block = """
═══════════════════════════════════
РЕЖИМ ОНБОРДИНГА — 5 ЭТАПОВ
═══════════════════════════════════

ТЫ ВЕДЁШЬ ПЕРВЫЙ РАЗГОВОР. Правила:
- Строго один вопрос за раз — жди ответ перед следующим
- Перед каждым этапом объявляй его: "📍 Этап N — Название"
- Реагируй живо: цепляйся за детали, уточняй если что-то важное
- Если человек говорит что не хочет онбординг → сразу напиши [PROFILE: onboarding_skipped=true] и скажи что готова работать, предложи /done
- Начиная с этапа 2 — анализируй стиль человека (длина сообщений, тон, эмодзи) и зеркали его
- Если видишь внутренний конфликт или сложность — мягко отрази и предложи вернуться позже
- Всё фиксируй в профиль через [PROFILE: ключ=значение]

───────────────────────────────────
ЭТАП 1 — ЗНАКОМСТВО
───────────────────────────────────
Вопросы по одному, в таком порядке:
1. Как тебя зовут? И сразу: как к тебе обращаться?
   → сохрани: [PROFILE: name=...] и [PROFILE: address=...]
2. Расскажи о себе — кто ты, где живёшь, чем занимаешься, что для тебя важно в жизни.
   Рассказывай открыто, если хочется добавить что-то ещё — не сдерживайся, пиши как есть.
   → сохрани: [PROFILE: occupation=...], [PROFILE: location=...], [PROFILE: values=...]
3. Какое у тебя сейчас время?
   → вычисли часовой пояс и сохрани: [PROFILE: timezone=UTC+N]

───────────────────────────────────
ЭТАП 2 — КАК ТЫ УСТРОЕН
───────────────────────────────────
1. Что тебя радует и заряжает в обычной жизни?
   → [PROFILE: energizers=...]
2. Как тебе комфортнее получать информацию — коротко и по делу или с деталями и объяснениями?
   → [PROFILE: info_style=brief/detailed]
3. Как реагируешь на прямую обратную связь — любишь честность или предпочитаешь мягче?
   → [PROFILE: feedback_style=direct/soft]
4. Как отдыхаешь и восстанавливаешься?
   → [PROFILE: recovery=...]

───────────────────────────────────
ЭТАП 3 — СФЕРЫ ЖИЗНИ
───────────────────────────────────
1. Покажи список и попроси оценить каждую от 1 до 10:
   Работа · Финансы · Здоровье · Отношения · Семья · Саморазвитие · Творчество · Отдых · Духовность · Окружение
   → сохрани: [PROFILE: spheres_score=работа:N,финансы:N,...]
2. В какой сфере хочешь прогресс в первую очередь?
   → [PROFILE: priority_sphere=...]

───────────────────────────────────
ЭТАП 4 — ВСЁ ЧТО ЕСТЬ ПРЯМО СЕЙЧАС
───────────────────────────────────
1. "Перенеси сюда всё что сейчас висит — в голове, в записях, в заметках, в мессенджерах.
   Всё подряд — дела, идеи, тревоги, планы. Нова сама всё распределит по категориям."
   → из ответа: добавляй [TASK: ...] для дел, [IDEA: ...] для идей
   → срочные дела — [TASK: ... | urgent | general | today]
   → планы на месяц — [TASK: ... | normal | general | month]

───────────────────────────────────
ЭТАП 5 — ЦЕЛИ, МЕЧТЫ, ИДЕИ
───────────────────────────────────
Вопросы по одному:
1. Есть большая цель или мечта?
   → [GOAL: ... | general | longterm]
2. Краткосрочные и долгосрочные цели?
   → [GOAL: ... | сфера | short/longterm]
3. Идеи и желания которые давно лежат?
   → [IDEA: ... | сфера]
4. Хобби и интересы?
   → [PROFILE: hobbies=...]
5. Что чаще всего мешает двигаться вперёд?
   → [PROFILE: pain=...]

───────────────────────────────────
ЗАВЕРШЕНИЕ
───────────────────────────────────
После всех этапов:
- Сделай короткое тёплое резюме о человеке — покажи что ты его услышала
- Скажи что готова работать
- Предложи /done чтобы открыть главное меню
"""

    return f"""Ты — Нова. Профессиональный личный ассистент.

{current_time}

ХАРАКТЕР И СТИЛЬ:
- Дружелюбная, тёплая, вдумчивая. Говоришь о себе "я", никогда не звучишь как бот.
- Не пишешь: "Конечно!", "Я понял!", "Как я могу помочь?" — только живые, человечные фразы.
- Обращаешься к человеку по имени/обращению из профиля когда уместно.
- Если человек пишет не на русском — отвечаешь на его языке.
- Замечаешь настроение и состояние — реагируешь с заботой.
- С каждым пользователем выстраиваешь персональные отношения на основе его профиля.

ГЛАВНАЯ ЗАДАЧА:
Разгружать голову. Принимать всё — задачи, идеи, планы — и превращать в чёткую структуру.
Фиксировать, распределять, отслеживать, напоминать, помогать двигаться вперёд.

ПРИОРИТЕТЫ:
1. Задачи и планирование — основное
2. Поддержка и советы
3. Сферы жизни — отслеживать баланс
4. Коучинг и рефлексия — только если человек просит

ФОРМАТИРОВАНИЕ (Telegram Markdown):
- *жирный* для важного, _курсив_ для акцентов
- Смайлики — уместно, не в каждой строке
- СТРОГО не более 4 строк в одном сообщении
- Без вступлений и предисловий — сразу по делу
- Списки через • когда нужно перечислить

ПРАВИЛО ПРО ЗАДАЧИ:
- "надо сделать", "запиши", "добавь" → фиксируй сам
- Человек рассуждает → спроси: "Добавить как задачу?"
- "хотелось бы", "мечтаю", "было бы здорово" → всегда фиксируй как идею без вопроса

УМНОЕ ПЛАНИРОВАНИЕ (обязательно):
- Если время задачи размыто ("на неделе", "как-нибудь", "скоро", "надо бы") — ВСЕГДА уточни конкретный день или предложи сам: "Поставить на вторник в 11:00?"
- Если у человека уже есть задачи — учти загруженность. Не ставь 5 срочных дел на один день.
- Для звонков, встреч, административных дел — предлагай конкретное время: утро (9-11), день (13-15), вечер (18-20).
- Если человек говорит "позвони", "запишись", "сходи" — это задача с временем. Спроси: "Когда удобнее — утром или днём?"
- После добавления задачи — кратко подтверди: что зафиксировала и когда.

РЕДАКТИРОВАНИЕ:
"удали задачу 5" → [DEL_TASK: 5]
"выполни задачу 3" → [DONE_TASK: 3]
"перенеси задачу 2 на неделю" → [EDIT_TASK: 2 | timeframe=week]
"измени задачу 1" → [EDIT_TASK: 1 | text=новый текст]
"прогресс по цели 4 — 60%" → [GOAL_PROGRESS: 4 | 60]

ЗАМОРОЖЕННЫЕ ЭЛЕМЕНТЫ:
Когда уместно — поднимай идеи и цели без движения. Предлагай запланировать.

ФИКСИРУЙ В КОНЦЕ ОТВЕТА (скрыто от пользователя):
[TASK: текст | приоритет | сфера | timeframe]
[GOAL: текст | сфера | timeframe]
[IDEA: текст | сфера]
[PROFILE: ключ=значение]
[DEL_TASK: id]
[DONE_TASK: id]
[EDIT_TASK: id | поле=значение]
[GOAL_PROGRESS: id | процент]

Приоритеты задач: urgent / important / normal
Timeframe: today / tomorrow / week / month / longterm
Время (5-е поле, опционально): формат HH:MM — указывай если пользователь назвал время или ты предлагаешь конкретный слот.
Пример с временем: [TASK: позвонить в ЖКХ | important | general | tomorrow | 11:00]
Пример без времени: [TASK: купить продукты | normal | general | today]
СФЕРЫ: {', '.join(SPHERES.values())}

GOOGLE CALENDAR:
{cal_block}

{onboarding_block}
{chr(10) + 'Профиль пользователя:' + chr(10) + profile_block if profile_block else ''}"""

async def _call_openrouter(messages, system, model):
    """Вызов через OpenRouter (OpenAI-compatible API)."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": WEBHOOK_URL,
        "X-Title": "Nova Assistant",
    }
    oai_messages = [{"role": "system", "content": system}] + messages
    data = {
        "model": model,
        "max_tokens": 700,
        "messages": oai_messages,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://openrouter.ai/api/v1/chat/completions",
                              headers=headers, json=data, timeout=45)
    result = r.json()
    if "choices" not in result or not result["choices"]:
        err = result.get("error", {}).get("message", str(result))
        logging.error(f"OpenRouter error: {err}")
        raise Exception(f"OpenRouter error: {err}")
    return result["choices"][0]["message"]["content"]


async def _call_claude_api(messages, system, model):
    """Вызов через Anthropic API."""
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {
        "model": model,
        "max_tokens": 700,
        "system": system,
        "messages": messages,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=45)
    result = r.json()
    if "content" not in result:
        err = result.get("error", {}).get("message", str(result))
        logging.error(f"Claude API error: {err}")
        raise Exception(f"Claude API error: {err}")
    return result["content"][0]["text"]


async def call_claude(messages, system, model=None):
    if model is None:
        model, provider = pick_model(messages)
    elif model == MODEL_SMART:
        provider = "claude"
    elif OPENROUTER_API_KEY:
        provider = "openrouter"
        model = MODEL_FAST_OPENROUTER
    else:
        provider = "claude"
    if provider == "openrouter":
        try:
            return await _call_openrouter(messages, system, model)
        except Exception as e:
            logging.warning(f"OpenRouter failed, fallback to Claude: {e}")
            return await _call_claude_api(messages, system, MODEL_FAST_CLAUDE)
    return await _call_claude_api(messages, system, model)

async def call_claude_vision(image_b64, system, prompt="Опиши что на фото и извлеки любые задачи, планы или важную информацию."):
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": MODEL_SMART,
        "max_tokens": 700,
        "system": system,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=60)
    return r.json()["content"][0]["text"]

_mem0_client = None

def get_mem0():
    global _mem0_client
    if _mem0_client is not None:
        return _mem0_client
    if not MEM0_API_KEY:
        return None
    try:
        from mem0 import MemoryClient
        _mem0_client = MemoryClient(api_key=MEM0_API_KEY)
        return _mem0_client
    except Exception as e:
        logging.warning(f"Mem0 init failed: {e}")
        return None

async def mem0_add(uid: int, messages: list):
    """Сохраняем новые сообщения диалога в Mem0 (async через executor)."""
    import asyncio
    mem = get_mem0()
    if not mem:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: mem.add(messages, user_id=str(uid))
        )
    except Exception as e:
        logging.warning(f"Mem0 add error: {e}")

async def mem0_search(uid: int, query: str) -> str:
    """Ищем релевантные воспоминания и возвращаем текстовый блок."""
    import asyncio
    mem = get_mem0()
    if not mem:
        return ""
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: mem.search(query, user_id=str(uid), limit=5)
        )
        if not results:
            return ""
        memories = [r.get("memory", "") for r in results if r.get("memory")]
        if not memories:
            return ""
        return "Долгосрочная память (из прошлых разговоров):\n" + "\n".join(f"• {m}" for m in memories)
    except Exception as e:
        logging.warning(f"Mem0 search error: {e}")
        return ""

async def call_groq_voice(audio_bytes):
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    if not GROQ_API_KEY: return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "ru"},
            timeout=30
        )
    if r.status_code == 200: return r.json().get("text")
    return None

async def process_response(uid, text):
    cal_lines = []
    # 5 полей: текст | приоритет | сфера | timeframe | HH:MM (время опционально)
    for match in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*([\w\W]+?)\s*\|\s*(\w+)\s*\|\s*(\d{1,2}:\d{2})\s*\]', text):
        add_task(uid, match[0], match[1], match[2], match[3])
        log_sphere_activity(uid, match[2])
        date_str = await add_to_calendar(uid, match[0], timeframe=match[3], event_time=match[4])
        if date_str:
            cal_lines.append(f"📆 Добавила в календарь: _{match[0]}_ — {date_str}")
    # 4 поля: текст | приоритет | сфера | timeframe
    for match in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*([\w\W]+?)\s*\|\s*(\w+)\s*\]', text):
        add_task(uid, match[0], match[1], match[2], match[3])
        log_sphere_activity(uid, match[2])
        date_str = await add_to_calendar(uid, match[0], timeframe=match[3])
        if date_str:
            cal_lines.append(f"📆 Добавила в календарь: _{match[0]}_ — {date_str}")
    # 3 поля (без timeframe)
    for t, p, s in re.findall(r'\[TASK:\s*([^|]+?)\s*\|\s*(\w+)\s*\|\s*([\w\W]+?)\s*\]', text):
        add_task(uid, t, p, s)
        date_str = await add_to_calendar(uid, t)
        if date_str:
            cal_lines.append(f"📆 Добавила в календарь: _{t}_ — {date_str}")
    for t, s, tf in re.findall(r'\[GOAL:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\]', text):
        add_goal(uid, t, s, tf)
    for t, s in re.findall(r'\[GOAL:\s*([^|]+?)\s*\|\s*(\w+)\s*\]', text):
        add_goal(uid, t, s)
    for gid, val in re.findall(r'\[GOAL_PROGRESS:\s*(\d+)\s*\|\s*(\d+)\s*\]', text):
        update_goal_progress(int(gid), int(val))
    for t, s in re.findall(r'\[IDEA:\s*(.+?)\s*\|\s*(\w+)\s*\]', text):
        add_idea(uid, t, s)
    for tid in re.findall(r'\[DONE_TASK:\s*(\d+)\s*\]', text):
        complete_task(int(tid))
    for tid in re.findall(r'\[DEL_TASK:\s*(\d+)\s*\]', text):
        delete_task(int(tid))
    for match in re.findall(r'\[EDIT_TASK:\s*(\d+)\s*\|\s*(.+?)\s*\]', text):
        tid = int(match[0])
        for pair in match[1].split('|'):
            if '=' in pair:
                k, _, v = pair.partition('=')
                edit_task(tid, **{k.strip(): v.strip()})
    profile_matches = re.findall(r'\[PROFILE:\s*(.+?)\s*\]', text)
    if profile_matches:
        profile = get_profile(uid)
        for m in profile_matches:
            for pair in m.split(','):
                if '=' in pair:
                    k, _, v = pair.partition('=')
                    profile[k.strip()] = v.strip()
        save_profile(uid, profile)
    # Управление событиями Google Calendar
    if re.search(r'\[CAL_DELETE_ALL\]', text):
        deleted = await clear_all_calendar_events(uid)
        if deleted > 0:
            cal_lines.append(f"🗑 Удалила {deleted} событий из календаря")
        else:
            cal_lines.append("🗑 События не найдены или Calendar не подключён")
    for event_id in re.findall(r'\[CAL_DELETE:\s*([^\]]+?)\s*\]', text):
        ok = await delete_calendar_event(uid, event_id.strip())
        if ok:
            cal_lines.append("🗑 Событие удалено из календаря")
    for cal_id in re.findall(r'\[CAL_DELETE_CALENDAR:\s*([^\]]+?)\s*\]', text):
        ok = await delete_extra_calendar(uid, cal_id.strip())
        if ok:
            cal_lines.append("🗑 Календарь удалён")
    for match in re.findall(r'\[CAL_UPDATE:\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^\]]*?)\s*\]', text):
        event_id, new_title, new_date, new_time = match
        ok = await update_calendar_event(uid, event_id.strip(),
            new_summary=new_title.strip() or None,
            new_date=new_date.strip() or None,
            new_time=new_time.strip() or None)
        if ok:
            cal_lines.append(f"✏️ Событие обновлено в календаре")

    text = re.sub(r'\[(TASK|GOAL|IDEA|PROFILE|DONE_TASK|DEL_TASK|EDIT_TASK|GOAL_PROGRESS|CAL_DELETE_ALL|CAL_DELETE|CAL_UPDATE|CAL_DELETE_CALENDAR):[^\]]*\]', '', text)
    text = re.sub(r'\[CAL_DELETE_ALL\]', '', text)
    result = text.strip()
    if cal_lines:
        result = result + "\n\n" + "\n".join(cal_lines)
    return result

async def send_safe(update, text, reply_markup=None):
    try:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    async def edit(text, kb=None):
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except:
            await query.edit_message_text(text, reply_markup=kb)

    if data == "onboarding_start":
        update_user(uid, onboarding_step=1)
        # Деактивируем кнопку, оставляя приветственный текст нетронутым
        disabled_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Начато", callback_data="noop")
        ]])
        try:
            await query.edit_message_reply_markup(reply_markup=disabled_kb)
        except Exception:
            pass
        profile = get_profile(uid)
        system = build_system(profile, onboarding_mode=True, uid=uid)
        try:
            response = await call_claude(
                get_history(uid) + [{"role": "user", "content": "Начинаем! Старт этапа 1."}],
                system, model=MODEL_SMART)
            clean = await process_response(uid, response)
            save_msg(uid, "user", "Начинаем!")
            save_msg(uid, "assistant", clean)
            await query.message.reply_text(clean, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Onboarding start error: {e}")
            await query.message.reply_text("Отлично, начнём! Как тебя зовут и как к тебе обращаться?")
        return
    if data == "noop":
        return
    if data == "back_main":
        await edit("Главное меню 👇"); return
    if data == "back_spheres":
        await edit("Выбери сферу:", spheres_keyboard()); return
    if data == "tasks_today":
        await edit(f"📅 *На сегодня:*\n\n{format_tasks(get_today_tasks(uid))}", tasks_keyboard()); return
    if data == "tasks_week":
        await edit(f"📆 *На неделю:*\n\n{format_tasks(get_tasks(uid, timeframe='week'))}", tasks_keyboard()); return
    if data == "tasks_month":
        await edit(f"🗓 *На месяц:*\n\n{format_tasks(get_tasks(uid, timeframe='month'))}", tasks_keyboard()); return
    if data == "tasks_longterm":
        await edit(f"♾ *Долгосрочные:*\n\n{format_tasks(get_tasks(uid, timeframe='longterm'))}", tasks_keyboard()); return
    if data == "tasks_urgent":
        await edit(f"🔴 *Срочные:*\n\n{format_tasks(get_tasks(uid, priority='urgent'))}", tasks_keyboard()); return
    if data == "tasks_done":
        await edit(f"✅ *Выполненные:*\n\n{format_tasks(get_tasks(uid, done=1))}", tasks_keyboard()); return
    if data == "tasks_all":
        tasks = get_tasks(uid)
        await edit(f"📋 *Все задачи:*\n\n{format_tasks(tasks)}", tasks_keyboard()); return
    if data == "goals_short":
        goals = get_goals(uid, timeframe="short")
        await edit(f"*⚡ Краткосрочные:*\n\n{format_goals(goals)}", goals_keyboard()); return
    if data == "goals_long":
        goals = get_goals(uid, timeframe="longterm")
        await edit(f"*🏔 Долгосрочные:*\n\n{format_goals(goals)}", goals_keyboard()); return
    if data == "goals_all":
        goals = get_goals(uid)
        await edit(f"*🎯 Все цели:*\n\n{format_goals(goals)}", goals_keyboard()); return
    if data.startswith("tdone_"):
        complete_task(int(data.replace("tdone_", "")))
        await edit("✅ Задача выполнена!", tasks_keyboard()); return
    if data.startswith("tdel_"):
        delete_task(int(data.replace("tdel_", "")))
        await edit("🗑 Задача удалена.", tasks_keyboard()); return
    if data.startswith("tmove_"):
        tid = data.replace("tmove_", "")
        await edit(f"Перенести задачу [{tid}] на:", move_timeframe_keyboard(tid)); return
    if data.startswith("tset_"):
        parts = data.split("_", 2)
        tf, tid = parts[1], int(parts[2])
        today = datetime.now().date().isoformat()
        tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
        if tf == "today":
            edit_task(tid, timeframe="today", due_date=today)
        elif tf == "tomorrow":
            edit_task(tid, timeframe="today", due_date=tomorrow)
        elif tf == "week":
            edit_task(tid, timeframe="week", due_date=None)
        elif tf == "month":
            edit_task(tid, timeframe="month", due_date=None)
        await edit("✅ Задача перенесена!", tasks_keyboard()); return
    if data.startswith("sphere_"):
        sk = data.replace("sphere_", "")
        log_sphere_activity(uid, sk)
        await edit(f"{SPHERES.get(sk)}\n\nЧто смотрим?", sphere_detail_keyboard(sk)); return
    if data.startswith("sph_tasks_"):
        sk = data.replace("sph_tasks_", "")
        await edit(f"{SPHERES.get(sk)} — задачи:\n\n{format_tasks(get_tasks(uid, sphere=sk))}", sphere_detail_keyboard(sk)); return
    if data.startswith("sph_goals_"):
        sk = data.replace("sph_goals_", "")
        goals = get_goals(uid, sphere=sk)
        await edit(f"{SPHERES.get(sk)} — цели:\n\n{format_goals(goals)}", sphere_detail_keyboard(sk)); return
    if data.startswith("sph_ideas_"):
        sk = data.replace("sph_ideas_", "")
        ideas = get_ideas(uid, sphere=sk)
        text = f"{SPHERES.get(sk)} — идеи:\n\n" + ("\n".join([f"• {i[1]}" for i in ideas]) if ideas else "Пусто 👌")
        await edit(text, sphere_detail_keyboard(sk)); return
    if data.startswith("mood_"):
        score = int(data.split("_")[1])
        log_mood(uid, score)
        profile = get_profile(uid)
        hist = get_mood_history(uid, days=7)
        avg = round(sum(r[0] for r in hist) / len(hist), 1) if hist else score
        comment = ""
        if len(hist) >= 3:
            trend = hist[-1][0] - hist[0][0]
            if trend > 2: comment = " Заметный рост за неделю 📈"
            elif trend < -2: comment = " Сдаёт немного — следи за собой 💛"
        await edit(f"Записала настроение: *{score}/10*{comment}\nСредний за неделю: {avg}/10")
        return
    if data.startswith("energy_"):
        score = int(data.split("_")[1])
        log_energy(uid, score)
        hist = get_energy_history(uid, days=7)
        avg = round(sum(r[0] for r in hist) / len(hist), 1) if hist else score
        await edit(f"Записала энергию: *{score}/10*\nСредняя за неделю: {avg}/10")
        return
    if data.startswith("habit_toggle_"):
        hid = int(data.replace("habit_toggle_", ""))
        mark_habit_today(uid, hid)
        await query.edit_message_reply_markup(reply_markup=habits_keyboard(uid))
        return
    if data == "habit_add":
        await edit("Напиши название привычки которую хочешь добавить:")
        context.user_data["mode"] = "habit_add"
        return
    if data in ("set_morning", "set_evening", "set_weekly"):
        key = {"set_morning": "notif_morning", "set_evening": "notif_evening", "set_weekly": "notif_weekly"}[data]
        profile = get_profile(uid)
        current = profile.get(key, "1")
        profile[key] = "0" if current != "0" else "1"
        save_profile(uid, profile)
        await query.edit_message_reply_markup(reply_markup=settings_keyboard(profile))
        return
    if data == "set_style":
        profile = get_profile(uid)
        profile["info_style"] = "brief" if profile.get("info_style", "detailed") == "detailed" else "detailed"
        save_profile(uid, profile)
        await query.edit_message_reply_markup(reply_markup=settings_keyboard(profile))
        return
    if data == "set_feedback":
        profile = get_profile(uid)
        profile["feedback_style"] = "direct" if profile.get("feedback_style", "soft") == "soft" else "soft"
        save_profile(uid, profile)
        await query.edit_message_reply_markup(reply_markup=settings_keyboard(profile))
        return
    if data == "set_tz":
        await edit("Напиши своё текущее время — например '15:30' или '9 утра'. Я вычислю часовой пояс.")
        context.user_data["mode"] = "set_tz"
        return

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)
    if user[1]:
        profile = get_profile(uid)
        name = profile.get("name", "")
        await send_safe(update, f"Я здесь, {name} 👋" if name else "Я здесь 👋", main_keyboard())
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Поехали!", callback_data="onboarding_start")]])
        await update.message.reply_text(ONBOARDING_INTRO, reply_markup=kb)
        save_msg(uid, "assistant", ONBOARDING_INTRO)

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_user(uid, onboarding_done=1)
    profile = get_profile(uid)
    system = build_system(profile, uid=uid)
    try:
        response = await call_claude(
            get_history(uid) + [{"role": "user", "content": "Знакомство завершено. Сделай краткий вывод — что знаешь обо мне и с чего начнём. Открой меню."}],
            system, model=MODEL_SMART)
        clean = await process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await send_safe(update, clean, main_keyboard())
    except:
        await send_safe(update, "Отлично, поехали! 🚀", main_keyboard())

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    existing = get_google_token(uid)
    if existing:
        # Проверяем что токен реально работает
        service = await get_calendar_service(uid)
        if service:
            await update.message.reply_text(
                "✅ Google Календарь подключён и работает!\n\nВсе новые задачи автоматически попадают в календарь.\n\nЧтобы переподключить — напиши /calreset",
                reply_markup=main_keyboard())
            return
        else:
            # Токен есть но сломан — удаляем и переподключаем
            db_exec("DELETE FROM google_tokens WHERE user_id=?", (uid,))
            await update.message.reply_text(
                "⚠️ Токен Calendar устарел, нужно переподключить.\nСейчас отправлю новую ссылку...",
                reply_markup=main_keyboard())
    try:
        flow = get_oauth_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=str(uid),
            prompt="consent"
        )
        await update.message.reply_text(
            f"📆 Подключаем Google Календарь!\n\nНажми на ссылку, войди в Google и разреши доступ:\n\n{auth_url}\n\nПосле авторизации бот автоматически получит доступ.",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        logging.error(f"Calendar auth error: {e}")
        await update.message.reply_text("Что-то пошло не так при подключении календаря(", reply_markup=main_keyboard())

async def cmd_calreset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db_exec("DELETE FROM google_tokens WHERE user_id=?", (uid,))
    await update.message.reply_text("🔄 Отключила Calendar. Напиши /calendar чтобы подключить заново.", reply_markup=main_keyboard())

async def cmd_calinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        lines = ["Диагностика Google Calendar:\n"]
        lines.append("GOOGLE_CLIENT_ID: " + ("есть" if GOOGLE_CLIENT_ID else "НЕТ - добавить в Railway Variables"))
        lines.append("GOOGLE_CLIENT_SECRET: " + ("есть" if GOOGLE_CLIENT_SECRET else "НЕТ - добавить в Railway Variables"))
        lines.append("WEBHOOK_URL: " + str(WEBHOOK_URL))
        try:
            token = get_google_token(uid)
            lines.append("Токен в базе: " + ("есть" if token else "нет - нужно /calendar"))
        except Exception as e:
            lines.append("Токен в базе: ошибка - " + str(e)[:100])
            token = None
        if token:
            try:
                service = await get_calendar_service(uid)
                lines.append("Токен рабочий: " + ("да" if service else "нет - нужно /calreset потом /calendar"))
                expired = getattr(token, 'expired', False)
                lines.append("Токен истёк: " + ("да" if expired else "нет"))
            except Exception as e:
                lines.append("Проверка токена: ошибка - " + str(e)[:100])
        await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
    except Exception as e:
        await update.message.reply_text("Ошибка диагностики: " + str(e)[:200])

async def cmd_newuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    for t in ["users","messages","tasks","goals","ideas","sphere_activity","google_tokens",
              "mood_log","energy_log","habits","habit_log","journal","wins",
              "sent_quotes","followup_queue"]:
        db_exec(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    await update.message.reply_text("Сброс выполнен. Напиши /start")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена.", reply_markup=main_keyboard())

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    if not profile:
        await update.message.reply_text("Профиль пока пустой.", reply_markup=main_keyboard()); return
    labels = {"name":"Имя","occupation":"Работа","goals":"Цели","pain":"Что мешает",
              "satisfied":"Что хорошо","day_rhythm":"Ритм дня","timezone":"Часовой пояс","character":"Характер","notes":"Заметки"}
    lines = ["*Что я знаю о тебе:*\n"]
    for k, l in labels.items():
        if profile.get(k): lines.append(f"*{l}:* {profile[k]}")
    await send_safe(update, "\n".join(lines), main_keyboard())

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_today_tasks(update.effective_user.id)
    await send_safe(update, f"📅 *На сегодня:*\n\n{format_tasks(tasks)}", main_keyboard())

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_tasks(update.effective_user.id, timeframe="week")
    await send_safe(update, f"📆 *На неделю:*\n\n{format_tasks(tasks)}", main_keyboard())

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await send_safe(update, format_week_plan(uid), main_keyboard())

async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    goals = get_goals(uid)
    if goals:
        await send_safe(update, f"*🎯 Твои цели:*\n\n{format_goals(goals)}", main_keyboard())
    else:
        await update.message.reply_text("Целей пока нет 🎯", reply_markup=main_keyboard())

async def cmd_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ideas = get_ideas(uid)
    if ideas:
        lines = ["*💡 Идеи и мечты:*\n"] + [f"• [{i[0]}] {i[1]}" for i in ideas]
        await send_safe(update, "\n".join(lines), main_keyboard())
    else:
        await update.message.reply_text("Идей пока нет 💡", reply_markup=main_keyboard())

async def cmd_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = get_tasks(uid)
    profile = get_profile(uid)
    system = build_system(profile, uid=uid)
    task_list = "\n".join([f"- ({t[2]}) {t[1]}" for t in tasks[:10]]) if tasks else "Задач нет"
    try:
        response = await call_claude(
            [{"role": "user", "content": f"Режим фокуса. Задачи:\n{task_list}\n\nОдна самая важная прямо сейчас — какая и почему?"}],
            system, model=MODEL_SMART)
        clean = await process_response(uid, response)
        await send_safe(update, clean, main_keyboard())
    except:
        await update.message.reply_text("Что-то пошло не так)", reply_markup=main_keyboard())

async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    system = build_system(profile, uid=uid)
    try:
        response = await call_claude(
            [{"role": "user", "content": "Проведи короткий чекин — спроси как я себя чувствую и какая энергия."}],
            system, model=MODEL_SMART)
        clean = await process_response(uid, response)
        await send_safe(update, clean, main_keyboard())
    except:
        await update.message.reply_text("Как ты сейчас? 🙂", reply_markup=main_keyboard())

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """*Нова — твой личный ассистент* 🤖

*📋 Задачи и планирование*
/today — задачи на сегодня
/week — план на неделю
/month — задачи на месяц
/focus — самая важная задача прямо сейчас
/brain — выгрузи всё из головы, я разберу

*🎯 Цели и развитие*
/goals — цели и прогресс в %
/wins — твои победы и достижения
/review — ежемесячный разбор прогресса

*💡 Идеи и рефлексия*
/ideas — идеи и желания
/reflect — вопрос для самоанализа
/ask — честный коучинговый ответ
/journal — личный дневник

*🌀 Трекеры*
/mood — настроение
/energy — уровень энергии
/habits — трекер привычек
/sphere — колесо жизни по сферам

*🔧 Настройки*
/checkin — чекин состояния
/profile — мой профиль
/settings — уведомления, часовой пояс, стиль
/calendar — Google Календарь
/report — отчёт, графики, PDF

Пишу в любом формате — текст, голос, фото 🎤📸"""
    await send_safe(update, text, main_keyboard())

async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = get_tasks(uid, timeframe="month")
    await send_safe(update, f"🗓 *На месяц:*\n\n{format_tasks(tasks)}", main_keyboard())

async def cmd_sphere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    spheres_score = profile.get("spheres_score", "")
    if spheres_score:
        lines = ["*🌀 Колесо жизни:*\n"]
        for pair in spheres_score.split(","):
            if ":" in pair:
                k, v = pair.strip().split(":", 1)
                try:
                    val = max(0, min(10, int(v.strip())))
                except ValueError:
                    val = 5
                bar = "█" * val + "░" * (10 - val)
                lines.append(f"{k.strip().capitalize()}: {bar} {val}/10")
        chart = generate_wheel_chart(uid)
        if chart:
            await context.bot.send_photo(uid, photo=chart)
        await send_safe(update, "\n".join(lines) + "\n\nОбновить оценки? Просто напиши мне новые.", main_keyboard())
    else:
        await send_safe(update, "Оценки по сферам ещё не заполнены. Пройди /start чтобы заполнить колесо жизни, или напиши мне оценки в свободной форме.", main_keyboard())

async def cmd_reflect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    q = random.choice(REFLECT_QUESTIONS)
    profile = get_profile(uid)
    address = profile.get("address") or profile.get("name") or ""
    prefix = f"{address}, " if address else ""
    await send_safe(update, f"🪞 *Вопрос для размышления:*\n\n_{prefix}{q}_", main_keyboard())
    set_followup(uid)

async def cmd_wins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    wins = get_wins(uid)
    if wins:
        lines = ["*🏆 Твои победы:*\n"]
        for w in wins:
            dt = w[1][:10] if w[1] else ""
            lines.append(f"• {w[0]}" + (f" _{dt}_" if dt else ""))
        await send_safe(update, "\n".join(lines), main_keyboard())
    else:
        await send_safe(update, "Побед пока нет — но это ненадолго 💪\nНапиши мне о любом своём достижении, я сохраню.", main_keyboard())

async def cmd_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Как твоё настроение сейчас? Выбери от 1 до 10:",
        reply_markup=score_keyboard("mood"))

async def cmd_habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    habits = get_habits(uid)
    if not habits:
        await send_safe(update, "Привычек пока нет. Напиши мне какую привычку хочешь отслеживать — я добавлю.", main_keyboard())
        return
    await update.message.reply_text("*📌 Трекер привычек — сегодня:*",
                                     parse_mode="Markdown", reply_markup=habits_keyboard(uid))

async def cmd_energy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Уровень энергии сейчас — от 1 до 10:",
        reply_markup=score_keyboard("energy"))

async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    system = build_system(profile, uid=uid)
    entries = get_journal_entries(uid, limit=3)
    context_block = ""
    if entries:
        context_block = "\nПоследние записи:\n" + "\n".join([f"— {e[0]}: {e[1][:60]}…" for e in entries])
    try:
        response = await call_claude(
            [{"role": "user", "content": f"Задай один глубокий вопрос для дневниковой записи. Учитывай профиль.{context_block}"}],
            system, model=MODEL_SMART)
        context.user_data["journal_question"] = response
        await send_safe(update, f"📔 *Дневник*\n\n{response}", None)
    except:
        q = "Что сегодня было для тебя самым значимым?"
        context.user_data["journal_question"] = q
        await send_safe(update, f"📔 *Дневник*\n\n{q}", None)

async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_safe(update,
        "🧠 *Выгрузка мыслей*\n\nПиши всё подряд — дела, идеи, тревоги, планы, случайные мысли. Не фильтруй. Я сама разберу по категориям.",
        None)
    context.user_data["mode"] = "brain"

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_safe(update,
        "🔍 *Честный анализ*\n\nЗадай мне вопрос о себе — и я отвечу честно, как зеркало. Без лишней мягкости.",
        None)
    context.user_data["mode"] = "ask"

async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    tasks = get_tasks(uid)
    goals = get_goals(uid)
    mood_hist = get_mood_history(uid, days=30)
    energy_hist = get_energy_history(uid, days=30)
    stats = get_sphere_stats(uid)

    avg_mood = round(sum(r[0] for r in mood_hist) / len(mood_hist), 1) if mood_hist else "нет данных"
    avg_energy = round(sum(r[0] for r in energy_hist) / len(energy_hist), 1) if energy_hist else "нет данных"
    goals_block = "\n".join([f"• {g[1]} — {g[4]}%" for g in goals[:5]]) if goals else "нет"
    stats_block = ", ".join([f"{k}: {v}д" for k, v in list(stats.items())[:5]]) if stats else "нет данных"

    system = build_system(profile, uid=uid)
    prompt = f"""Сделай ежемесячный разбор для пользователя.

Открытых задач: {len(tasks)}
Активных целей:\n{goals_block}
Ср. настроение за месяц: {avg_mood}/10
Ср. энергия за месяц: {avg_energy}/10
Активность по сферам: {stats_block}

Структура:
1. Что работает — честно и конкретно
2. Что не работает — без осуждения
3. Паттерны которые ты замечаешь
4. Одна главная рекомендация на следующий месяц
5. Короткое вдохновляющее завершение

Стиль: глубокий, честный, поддерживающий."""

    await update.message.reply_text("Готовлю ежемесячный разбор... 📊")
    try:
        response = await call_claude([{"role": "user", "content": prompt}], system, model=MODEL_SMART)
        chart = generate_sphere_chart(uid)
        if chart:
            await context.bot.send_photo(uid, photo=chart, caption="📊 Активность и прогресс")
        await send_safe(update, response, main_keyboard())
    except Exception as e:
        logging.error(f"Review error: {e}")
        await update.message.reply_text("Что-то пошло не так(", reply_markup=main_keyboard())

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    tz = profile.get("timezone", "не задан")
    style = "подробно" if profile.get("info_style", "detailed") == "detailed" else "кратко"
    feedback = "мягкий" if profile.get("feedback_style", "soft") == "soft" else "прямой"
    text = f"*⚙️ Настройки*\n\nЧасовой пояс: {tz}\nСтиль ответов: {style}\nТон: {feedback}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=settings_keyboard(profile))

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    clear_followup(uid)
    await update.message.reply_text("Слушаю... 🎤")
    try:
        if not os.environ.get("GROQ_API_KEY"):
            await update.message.reply_text(
                "⚠️ Голосовые сообщения не настроены.\n\nНужно добавить GROQ_API_KEY в Railway → Variables.\nПолучи бесплатный ключ на console.groq.com")
            return
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
        text = await call_groq_voice(bytes(audio_bytes))
        if not text:
            await update.message.reply_text("Не смогла расшифровать( Попробуй ещё раз.")
            return
        user = get_user(uid)
        profile = get_profile(uid)
        onboarding_done = user[1]
        system = build_system(profile, onboarding_mode=not onboarding_done, uid=uid)
        history = get_history(uid)
        history.append({"role": "user", "content": text})
        save_msg(uid, "user", f"[голосовое] {text}")
        response = await call_claude(history, system)
        clean = await process_response(uid, response)
        save_msg(uid, "assistant", clean)
        if "?" in clean:
            set_followup(uid)
        await send_safe(update, f"_Ты сказала:_ {text}\n\n{clean}", main_keyboard() if onboarding_done else None)
    except Exception as e:
        logging.error(f"Voice error uid={uid}: {e}")
        await update.message.reply_text("Не смогла обработать голосовое( Попробуй ещё раз.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    clear_followup(uid)
    await update.message.reply_text("Смотрю фото... 👀")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    image_b64 = base64.b64encode(bytes(photo_bytes)).decode('utf-8')
    caption = update.message.caption or ""
    profile = get_profile(uid)
    user = get_user(uid)
    system = build_system(profile, onboarding_mode=not user[1], uid=uid)
    prompt = f"Пользователь прислал фото. {'Подпись: ' + caption if caption else ''} Опиши что видишь, извлеки задачи, планы, важную информацию."
    try:
        response = await call_claude_vision(image_b64, system, prompt)
        clean = await process_response(uid, response)
        save_msg(uid, "user", f"[фото] {caption}")
        save_msg(uid, "assistant", clean)
        await send_safe(update, clean, main_keyboard() if user[1] else None)
    except Exception as e:
        logging.error(f"Photo error: {e}")
        await update.message.reply_text("Не смогла обработать фото( Попробуй ещё раз.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    clear_followup(uid)
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith('text'):
        await update.message.reply_text("Пока умею читать только текстовые файлы (.txt, .md)")
        return
    await update.message.reply_text("Читаю документ... 📄")
    file = await context.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()
    try:
        text_content = file_bytes.decode('utf-8')
    except:
        text_content = file_bytes.decode('latin-1')
    if len(text_content) > 3000:
        text_content = text_content[:3000] + "...[обрезано]"
    profile = get_profile(uid)
    user = get_user(uid)
    system = build_system(profile, onboarding_mode=not user[1], uid=uid)
    history = get_history(uid)
    history.append({"role": "user", "content": f"Я прислала документ '{doc.file_name}':\n\n{text_content}\n\nПроанализируй, извлеки задачи и важную информацию."})
    save_msg(uid, "user", f"[документ: {doc.file_name}]")
    try:
        response = await call_claude(history, system)
        clean = await process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await send_safe(update, clean, main_keyboard() if user[1] else None)
    except Exception as e:
        logging.error(f"Doc error: {e}")
        await update.message.reply_text("Не смогла обработать документ(")

async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    clear_followup(uid)
    text = update.message.text or update.message.caption or ""
    if not text:
        await update.message.reply_text("Пересланное сообщение без текста — не могу обработать(")
        return
    profile = get_profile(uid)
    user = get_user(uid)
    system = build_system(profile, onboarding_mode=not user[1], uid=uid)
    history = get_history(uid)
    history.append({"role": "user", "content": f"Я переслала сообщение:\n\n{text}\n\nОбработай — извлеки задачи, важную информацию или просто прокомментируй."})
    save_msg(uid, "user", f"[пересланное] {text[:100]}")
    try:
        response = await call_claude(history, system)
        clean = await process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await send_safe(update, clean, main_keyboard() if user[1] else None)
    except:
        await update.message.reply_text("Что-то пошло не так)")

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    if text == "📋 Задачи":
        await update.message.reply_text("Выбери:", reply_markup=tasks_keyboard()); return True
    if text == "🎯 Цели":
        await update.message.reply_text("Выбери:", reply_markup=goals_keyboard()); return True
    if text == "🌀 Сферы жизни":
        await update.message.reply_text("Выбери сферу:", reply_markup=spheres_keyboard()); return True
    if text == "💡 Идеи":
        ideas = get_ideas(uid)
        if ideas:
            lines = ["*💡 Идеи и мечты:*\n"] + [f"• [{i[0]}] {i[1]}" for i in ideas]
            await send_safe(update, "\n".join(lines), main_keyboard())
        else:
            await update.message.reply_text("Идей пока нет... поделись)", reply_markup=main_keyboard())
        return True
    if text == "📊 Дашборд":
        await update.message.reply_text(format_dashboard(uid), reply_markup=main_keyboard()); return True
    if text == "📅 План недели":
        await send_safe(update, format_week_plan(uid), main_keyboard()); return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)

    if getattr(update.message, 'forward_origin', None) or getattr(update.message, 'forward_from', None) or getattr(update.message, 'forward_from_chat', None):
        await handle_forward(update, context)
        return

    user = get_user(uid)
    if not user:
        await update.message.reply_text("Привет! Напиши /start чтобы начать.")
        return
    if await handle_menu_button(update, context):
        return

    text = update.message.text
    if not text:
        return

    profile = get_profile(uid)
    onboarding_done = user[1]

    # Загружаем события и список календарей если пользователь управляет Calendar
    cal_events = None
    _cal_keywords = ("удали", "очисти", "убери", "перенеси", "измени", "редактир",
                     "событи", "календар", "папк", "группу", "список")
    if get_google_token(uid) and any(kw in text.lower() for kw in _cal_keywords):
        try:
            cal_events = await list_calendar_events(uid, max_results=30, include_past=True)
            cals = await list_calendars(uid)
            if cals:
                cal_events = cal_events or []
                cal_events.append({"_calendars": cals})
        except Exception as e:
            logging.warning(f"Calendar context load error: {e}")

    system = build_system(profile, onboarding_mode=not onboarding_done, uid=uid, cal_events=cal_events)

    if onboarding_done:
        tasks = get_tasks(uid)
        if tasks:
            system += "\n\nАктуальные задачи:\n" + "\n".join([f"[{t[0]}] ({t[2]}) {t[1]}" for t in tasks[:10]])
        frozen = get_frozen_items(uid)
        if frozen and len(tasks) == 0:
            items_text = "\n".join([f"- {f[1]}" for f in frozen])
            system += f"\n\nЗамороженные идеи/цели (давно без движения):\n{items_text}\nЕсли уместно — предложи запланировать одну из них."

    # Секретные команды сброса
    if text.strip().lower() in ("полный сброс", "full reset"):
        for t in ["users","messages","tasks","goals","ideas","sphere_activity","google_tokens",
                  "mood_log","energy_log","habits","habit_log","journal","wins","sent_quotes","followup_queue"]:
            db_exec(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        await update.message.reply_text("Полный сброс выполнен. Напиши /start")
        return
    if text.strip().lower() == "сброс истории":
        clear_history(uid)
        await update.message.reply_text("История диалога очищена.", reply_markup=main_keyboard())
        return

    # Режим выгрузки мыслей
    mode = context.user_data.get("mode")
    if mode == "brain":
        context.user_data.pop("mode", None)
        profile = get_profile(uid)
        system = build_system(profile, uid=uid)
        try:
            response = await call_claude(
                [{"role": "user", "content": f"Пользователь выгружает всё из головы. Разбери по категориям: задачи, идеи, тревоги, цели. Для каждой категории дай короткий список. Текст:\n\n{text}"}],
                system, model=MODEL_SMART)
            clean = await process_response(uid, response)
            save_msg(uid, "user", f"[brain dump] {text[:100]}")
            save_msg(uid, "assistant", clean)
            await send_safe(update, clean, main_keyboard())
        except:
            await update.message.reply_text("Что-то пошло не так(", reply_markup=main_keyboard())
        return
    # Режим честного анализа
    if mode == "ask":
        context.user_data.pop("mode", None)
        profile = get_profile(uid)
        system = build_system(profile, uid=uid)
        try:
            response = await call_claude(
                [{"role": "user", "content": f"Пользователь задаёт вопрос о себе и просит честный коучинговый ответ — как зеркало, без лишней мягкости, но с уважением. Вопрос: {text}"}],
                system, model=MODEL_SMART)
            clean = await process_response(uid, response)
            save_msg(uid, "user", f"[ask] {text}")
            save_msg(uid, "assistant", clean)
            await send_safe(update, clean, main_keyboard())
        except:
            await update.message.reply_text("Что-то пошло не так(", reply_markup=main_keyboard())
        return
    # Режим дневника — ответ на вопрос
    if "journal_question" in context.user_data:
        question = context.user_data.pop("journal_question")
        save_journal(uid, question, text)
        await send_safe(update, "Записала в дневник 📔", main_keyboard())
        return
    # Добавление привычки
    if mode == "habit_add":
        context.user_data.pop("mode", None)
        get_or_create_habit(uid, text.strip())
        await send_safe(update, f"Привычка «{text.strip()}» добавлена! Отмечай каждый день через /habits 💪", main_keyboard())
        return
    # Установка часового пояса через текст
    if mode == "set_tz":
        context.user_data.pop("mode", None)
        profile = get_profile(uid)
        system = build_system(profile, uid=uid)
        try:
            response = await call_claude(
                [{"role": "user", "content": f"Пользователь написал своё текущее время: '{text}'. Вычисли UTC offset и ответь только строкой вида UTC+N или UTC-N."}],
                system)
            tz_str = response.strip().split()[0]
            profile["timezone"] = tz_str
            save_profile(uid, profile)
            await send_safe(update, f"Часовой пояс обновлён: {tz_str}", main_keyboard())
        except:
            await update.message.reply_text("Не смог вычислить часовой пояс. Напиши в формате UTC+3", reply_markup=main_keyboard())
        return

    # Детектим когда пользователь говорит о победе/достижении
    win_keywords = ("сделал", "завершил", "закончил", "выполнил", "достиг", "получил", "удалось", "победил", "справился")
    if any(kw in text.lower() for kw in win_keywords) and len(text) < 200:
        context.user_data["potential_win"] = text

    clear_followup(uid)

    history = get_history(uid)
    history.append({"role": "user", "content": text})
    save_msg(uid, "user", text)

    # Обогащаем системный промпт релевантными воспоминаниями из Mem0
    mem_block = await mem0_search(uid, text)
    enriched_system = system + (f"\n\n{mem_block}" if mem_block else "")

    try:
        model = MODEL_SMART if not onboarding_done else None
        response = await call_claude(history, enriched_system, model=model)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так... попробуй ещё раз)"); return

    clean = await process_response(uid, response)
    save_msg(uid, "assistant", clean)
    if "?" in clean:
        set_followup(uid)

    # Сохраняем диалог в Mem0 для долгосрочной памяти
    await mem0_add(uid, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": clean},
    ])

    # Предлагаем отметить победу если Нова закрыла задачу
    if "potential_win" in context.user_data:
        if any(kw in clean.lower() for kw in ("выполнен", "закрыт", "готово", "сделан", "✅")):
            win_text = context.user_data.pop("potential_win")
            add_win(uid, win_text)
        else:
            context.user_data.pop("potential_win", None)

    await send_safe(update, clean, main_keyboard() if onboarding_done else None)

async def morning(context):
    utc_now = datetime.now(timezone.utc)
    users = db_fetch("SELECT user_id, profile FROM users WHERE onboarding_done=1")
    for uid, pj in users:
        profile = json.loads(pj)
        local_now = utc_now + timedelta(hours=get_user_tz_offset(profile))
        if local_now.hour != 8:
            continue
        address = profile.get("address") or profile.get("name") or ""
        today_tasks = get_today_tasks(uid)
        urgent = get_tasks(uid, priority="urgent")
        goals = get_goals(uid)
        quote_text, quote_author = get_random_quote(uid)
        notif_extras = profile.get("notif_extras", "")

        task_block = ""
        if today_tasks:
            task_block = f"Задачи на сегодня:\n" + "\n".join([f"• {t[1]}" for t in today_tasks[:5]])
        elif urgent:
            task_block = f"Срочных задач: {len(urgent)}"

        goal_block = ""
        if goals:
            goal_block = "Активных целей: " + str(len(goals))

        system = build_system(profile, uid=uid)
        prompt = f"""Сгенерируй утреннее уведомление для пользователя. Используй эти данные:

Обращение: {address}
Дата: {local_now.strftime('%d.%m.%Y, %A')}
Цитата дня: «{quote_text}» — {quote_author}
{task_block}
{goal_block}
{f"Дополнительно включи: {notif_extras}" if notif_extras else ""}

Структура:
1. Тёплое приветствие с датой
2. Цитата дня — выдели курсивом, подпись автора
3. Один вопрос для самоанализа на сегодня (связан с целями или ситуацией пользователя)
4. Задачи на сегодня (если есть)
5. Короткое напутствие

Стиль: живой, тёплый, не формальный. Не больше 10 строк суммарно."""

        try:
            response = await call_claude([{"role": "user", "content": prompt}], system, model=MODEL_SMART)
            await context.bot.send_message(uid, response, parse_mode="Markdown")
            save_msg(uid, "assistant", response)
            set_followup(uid)
        except Exception as e:
            logging.error(f"Morning notif error {uid}: {e}")

async def evening(context):
    utc_now = datetime.now(timezone.utc)
    users = db_fetch("SELECT user_id, profile FROM users WHERE onboarding_done=1")
    for uid, pj in users:
        profile = json.loads(pj)
        local_now = utc_now + timedelta(hours=get_user_tz_offset(profile))
        if local_now.hour != 19:
            continue
        address = profile.get("address") or profile.get("name") or ""
        tasks = get_tasks(uid)
        today_str = (utc_now + timedelta(hours=get_user_tz_offset(profile))).date().isoformat()
        done_today = db_fetch("""SELECT text FROM tasks WHERE user_id=? AND done=1
                                  AND done_at >= ?""", (uid, today_str))
        stats = get_sphere_stats(uid)
        inactive = set(SPHERE_KEYS) - set(stats.keys())

        system = build_system(profile, uid=uid)
        inactive_labels = ", ".join([SPHERES[s] for s in list(inactive)[:3]]) if inactive else ""
        done_block = "\n".join([f"• {t[0]}" for t in done_today[:5]]) if done_today else "Нет данных"

        prompt = f"""Сгенерируй вечернее уведомление для пользователя.

Обращение: {address}
Выполнено сегодня: {done_block}
Открытых задач осталось: {len(tasks)}
Сферы без внимания сегодня: {inactive_labels or 'все активны'}

Структура:
1. Тёплое вечернее приветствие
2. Короткий итог дня — что сделано (не перечисляй всё, обобщи)
3. Один вопрос для рефлексии — что дал этот день, что можно было сделать иначе
4. Одно намерение или фокус на завтра
5. Тёплое завершение — не сухое

Стиль: мягкий, заботливый, человечный. Не более 8 строк."""

        try:
            response = await call_claude([{"role": "user", "content": prompt}], system, model=MODEL_SMART)
            await context.bot.send_message(uid, response, parse_mode="Markdown")
            save_msg(uid, "assistant", response)
            set_followup(uid)
        except Exception as e:
            logging.error(f"Evening notif error {uid}: {e}")

async def weekly_review(context):
    utc_now = datetime.now(timezone.utc)
    users = db_fetch("SELECT user_id, profile FROM users WHERE onboarding_done=1")
    for uid, pj in users:
        profile = json.loads(pj)
        local_now = utc_now + timedelta(hours=get_user_tz_offset(profile))
        if local_now.hour != 10 or local_now.weekday() != 6:
            continue
        address = profile.get("address") or profile.get("name") or ""
        tasks = get_tasks(uid)
        goals = get_goals(uid)
        frozen = get_frozen_items(uid)
        stats = get_sphere_stats(uid)

        goals_block = "\n".join([f"• {g[1]} — {g[4]}%" for g in goals[:6]]) if goals else "Целей нет"
        stats_block = "\n".join([f"• {SPHERES.get(k,k)}: {v} дн." for k,v in stats.items()]) if stats else "Нет данных"
        frozen_block = "\n".join([f"• {f[1]}" for f in frozen[:3]]) if frozen else ""

        system = build_system(profile, uid=uid)
        prompt = f"""Сгенерируй еженедельный обзор для пользователя.

Обращение: {address}
Неделя: {(local_now - timedelta(days=6)).strftime('%d.%m')} — {local_now.strftime('%d.%m.%Y')}
Открытых задач: {len(tasks)}
Прогресс целей:
{goals_block}
Активность по сферам за неделю:
{stats_block}
{f"Давно без движения:{chr(10)}{frozen_block}" if frozen_block else ""}

Структура:
1. Приветствие с неделей
2. Итоги по сферам — что активно, что игнорируется
3. Прогресс целей — честно и поддерживающе
4. Планы и фокус на следующую неделю
5. Если есть замороженные — мягко поднять
6. Завершение — вдохновляющее

Стиль: глубже обычного, аналитично но по-человечески. Не более 12 строк."""

        try:
            response = await call_claude([{"role": "user", "content": prompt}], system, model=MODEL_SMART)

            # Отправляем график перед текстом
            chart = generate_sphere_chart(uid)
            if chart:
                await context.bot.send_photo(uid, photo=chart,
                    caption="📊 Прогресс по сферам и целям за неделю")

            await context.bot.send_message(uid, response, parse_mode="Markdown")
            save_msg(uid, "assistant", response)

            # Предлагаем PDF
            await context.bot.send_message(uid, "Хочешь подробный PDF отчёт? Напиши /report")
        except Exception as e:
            logging.error(f"Weekly review error {uid}: {e}")

async def check_followup(context):
    pending = get_pending_followups()
    for uid, asked_at, attempts in pending:
        profile = get_profile(uid)
        history = get_history(uid, limit=6)
        system = build_system(profile, uid=uid)
        try:
            response = await call_claude(
                history + [{"role": "user", "content":
                    "Я не ответил на твой последний вопрос. Переформулируй его иначе — коротко, с другой стороны. "
                    "Не упоминай что я молчал."}],
                system, model=MODEL_SMART)
            clean = await process_response(uid, response)
            await context.bot.send_message(uid, clean, parse_mode="Markdown")
            db_exec("UPDATE followup_queue SET asked_at=?, attempts=? WHERE user_id=?",
                    (datetime.now().isoformat(), attempts + 1, uid))
        except Exception as e:
            logging.error(f"Followup error {uid}: {e}")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("Генерирую отчёт... 📊")
    chart = generate_sphere_chart(uid)
    if chart:
        await context.bot.send_photo(uid, photo=chart, caption="📊 Прогресс по сферам и целям")
    else:
        await update.message.reply_text("Нет данных для графика — добавь задачи и цели.")
    pdf = generate_pdf_report(uid)
    if pdf:
        await context.bot.send_document(uid, document=pdf, filename="nova_report.pdf",
                                        caption="📄 Подробный отчёт")
    else:
        await update.message.reply_text("PDF недоступен (библиотека reportlab не установлена).",
                                        reply_markup=main_keyboard())

from aiohttp import web

async def oauth_callback(request):
    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")
    if not code or not state:
        return web.Response(text="Ошибка авторизации — нет кода или state")
    try:
        import asyncio
        uid = int(state)
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            await request.app["bot"].send_message(uid,
                "❌ Calendar не настроен: отсутствуют GOOGLE_CLIENT_ID или GOOGLE_CLIENT_SECRET в переменных Railway.\n\nНужно добавить их в Railway → Variables.")
            return web.Response(text="Ошибка: Google credentials не настроены на сервере.")
        flow = get_oauth_flow()
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: flow.fetch_token(code=code)
        )
        creds = flow.credentials
        save_google_token(uid, creds)
        await request.app["bot"].send_message(
            uid,
            "✅ Google Календарь успешно подключён!\n\nТеперь все твои задачи будут автоматически появляться в календаре 📆"
        )
        return web.Response(text="✅ Готово! Можешь закрыть эту вкладку и вернуться в Telegram.")
    except Exception as e:
        logging.error(f"OAuth callback error: {e}")
        try:
            uid = int(state)
            await request.app["bot"].send_message(uid,
                f"❌ Ошибка подключения Calendar:\n`{str(e)[:300]}`\n\nОтправь этот текст разработчику.")
        except Exception:
            pass
        return web.Response(text=f"Ошибка: {e}")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("done",    cmd_done))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("today",   cmd_today))
    app.add_handler(CommandHandler("week",    cmd_week))
    app.add_handler(CommandHandler("month",   cmd_month))
    app.add_handler(CommandHandler("goals",   cmd_goals))
    app.add_handler(CommandHandler("ideas",   cmd_ideas))
    app.add_handler(CommandHandler("focus",   cmd_focus))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CommandHandler("sphere",  cmd_sphere))
    app.add_handler(CommandHandler("reflect", cmd_reflect))
    app.add_handler(CommandHandler("wins",    cmd_wins))
    app.add_handler(CommandHandler("mood",    cmd_mood))
    app.add_handler(CommandHandler("habits",  cmd_habits))
    app.add_handler(CommandHandler("energy",  cmd_energy))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("brain",   cmd_brain))
    app.add_handler(CommandHandler("ask",     cmd_ask))
    app.add_handler(CommandHandler("review",  cmd_review))
    app.add_handler(CommandHandler("calendar",cmd_calendar))
    app.add_handler(CommandHandler("calreset",cmd_calreset))
    app.add_handler(CommandHandler("calinfo",cmd_calinfo))
    app.add_handler(CommandHandler("report",  cmd_report))
    app.add_handler(CommandHandler("settings",cmd_settings))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("plan",    cmd_plan))
    # Скрытые команды — не показываются в меню BotFather
    app.add_handler(CommandHandler("reset",   cmd_reset))
    app.add_handler(CommandHandler("newuser", cmd_newuser))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    jq = app.job_queue
    jq.run_repeating(morning, interval=3600, first=60)
    jq.run_repeating(evening, interval=3600, first=120)
    jq.run_repeating(weekly_review, interval=3600, first=180)
    jq.run_repeating(check_followup, interval=1800, first=300)

    from telegram import BotCommand

    BOT_COMMANDS = [
        BotCommand("start",    "Знакомство и онбординг"),
        BotCommand("help",     "Что умею и как работать"),
        BotCommand("today",    "Задачи на сегодня"),
        BotCommand("week",     "План на неделю"),
        BotCommand("month",    "План на месяц"),
        BotCommand("goals",    "Цели и прогресс"),
        BotCommand("ideas",    "Идеи и желания"),
        BotCommand("focus",    "Главная задача прямо сейчас"),
        BotCommand("checkin",  "Чекин состояния и энергии"),
        BotCommand("sphere",   "Колесо жизни"),
        BotCommand("reflect",  "Вопрос для самоанализа"),
        BotCommand("wins",     "Победы и достижения"),
        BotCommand("mood",     "Трекер настроения"),
        BotCommand("habits",   "Трекер привычек"),
        BotCommand("energy",   "Отметить уровень энергии"),
        BotCommand("journal",  "Личный дневник"),
        BotCommand("brain",    "Выгрузка мыслей"),
        BotCommand("ask",      "Честный анализ от Новы"),
        BotCommand("review",   "Ежемесячный разбор"),
        BotCommand("calendar", "Google Календарь"),
        BotCommand("report",   "Отчёт, графики, PDF"),
        BotCommand("settings", "Настройки"),
        BotCommand("profile",  "Мой профиль"),
    ]

    async def start_web(app_obj):
        await app_obj.bot.set_my_commands(BOT_COMMANDS)
        logging.info("Bot commands registered")
        web_app = web.Application()
        web_app["bot"] = app_obj.bot
        web_app.router.add_get("/oauth/callback", oauth_callback)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
        await site.start()
        logging.info("Web server started")

    app.post_init = start_web
    logging.info("Nova is running...")
    app.run_polling()

if __name__ == "__main__":
    main()