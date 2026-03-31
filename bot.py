import os, sys, httpx, sqlite3, json, logging, re
from datetime import datetime, time as dtime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
TURSO_URL = os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")

# ── БАЗА ДАННЫХ (Turso или локальная SQLite) ──

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        import libsql_experimental as libsql
        conn = libsql.connect("nova.db", sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
        conn.sync()
        return conn
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
        due_date TEXT,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        sphere TEXT DEFAULT 'general',
        timeframe TEXT DEFAULT 'longterm',
        progress INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        sphere TEXT DEFAULT 'general',
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sphere_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, sphere TEXT,
        activity_date TEXT)""")
    conn.commit()
    if hasattr(conn, 'sync'): conn.sync()
    conn.close()

def db_exec(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    if hasattr(conn, 'sync'): conn.sync()
    conn.close()

def db_fetch(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows

def db_fetchone(query, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(query, params)
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
    db_exec("INSERT INTO tasks (user_id,text,priority,sphere,timeframe,due_date,created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, text, priority, sphere, timeframe, due_date, datetime.now().isoformat()))

def get_tasks(uid, sphere=None, timeframe=None, priority=None, done=0):
    query = "SELECT id,text,priority,sphere,timeframe,due_date FROM tasks WHERE user_id=? AND done=?"
    params = [uid, done]
    if sphere: query += " AND sphere=?"; params.append(sphere)
    if timeframe: query += " AND timeframe=?"; params.append(timeframe)
    if priority: query += " AND priority=?"; params.append(priority)
    query += " ORDER BY id"
    return db_fetch(query, params)

def get_today_tasks(uid):
    today = datetime.now().date().isoformat()
    return db_fetch("""SELECT id,text,priority,sphere,timeframe,due_date FROM tasks
                 WHERE user_id=? AND done=0 AND (timeframe='today' OR due_date=?)
                 ORDER BY priority DESC""", (uid, today))

def complete_task(task_id):
    db_exec("UPDATE tasks SET done=1 WHERE id=?", (task_id,))

def add_goal(uid, text, sphere="general", timeframe="longterm"):
    db_exec("INSERT INTO goals (user_id,text,sphere,timeframe,created_at) VALUES (?,?,?,?,?)",
            (uid, text, sphere, timeframe, datetime.now().isoformat()))

def get_goals(uid, sphere=None, timeframe=None):
    query = "SELECT id,text,sphere,timeframe,progress FROM goals WHERE user_id=? AND done=0"
    params = [uid]
    if sphere: query += " AND sphere=?"; params.append(sphere)
    if timeframe: query += " AND timeframe=?"; params.append(timeframe)
    return db_fetch(query, params)

def add_idea(uid, text, sphere="general"):
    db_exec("INSERT INTO ideas (user_id,text,sphere,created_at) VALUES (?,?,?,?)",
            (uid, text, sphere, datetime.now().isoformat()))

def get_ideas(uid, sphere=None):
    if sphere:
        return db_fetch("SELECT id,text,sphere FROM ideas WHERE user_id=? AND sphere=?", (uid, sphere))
    return db_fetch("SELECT id,text,sphere FROM ideas WHERE user_id=?", (uid,))

def log_sphere_activity(uid, sphere):
    db_exec("INSERT INTO sphere_activity (user_id,sphere,activity_date) VALUES (?,?,?)",
            (uid, sphere, datetime.now().date().isoformat()))

def get_sphere_stats(uid):
    rows = db_fetch("""SELECT sphere, COUNT(*) FROM sphere_activity
                 WHERE user_id=? AND activity_date >= date('now', '-7 days')
                 GROUP BY sphere ORDER BY COUNT(*) DESC""", (uid,))
    return {r[0]: r[1] for r in rows}

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
        [KeyboardButton("📊 Дашборд")]
    ], resize_keyboard=True)

def tasks_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 На сегодня", callback_data="tasks_today"),
         InlineKeyboardButton("📆 На неделю", callback_data="tasks_week")],
        [InlineKeyboardButton("🗓 На месяц", callback_data="tasks_month"),
         InlineKeyboardButton("♾ Долгосрочные", callback_data="tasks_longterm")],
        [InlineKeyboardButton("🔴 Срочные", callback_data="tasks_urgent"),
         InlineKeyboardButton("✅ Выполненные", callback_data="tasks_done")],
        [InlineKeyboardButton("📋 Все", callback_data="tasks_all")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
    ])

def goals_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Краткосрочные", callback_data="goals_short"),
         InlineKeyboardButton("🏔 Долгосрочные", callback_data="goals_long")],
        [InlineKeyboardButton("📋 Все цели", callback_data="goals_all")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
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
        [InlineKeyboardButton("💡 Идеи", callback_data=f"sph_ideas_{sphere_key}")],
        [InlineKeyboardButton("⬅️ К сферам", callback_data="back_spheres")]
    ])

def format_tasks(tasks, show_timeframe=False):
    if not tasks: return "Пусто)"
    icons = {"urgent": "🔴", "important": "🟡", "normal": "⚪"}
    lines = []
    for t in tasks:
        icon = icons.get(t[2], "⚪")
        line = f"{icon} [{t[0]}] {t[1]}"
        lines.append(line)
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
    lines = [
        f"📊 {name} — {now.strftime('%d.%m.%Y')}\n",
        f"Сегодня: {len(today_tasks)} задач",
        f"Всего открытых: {len(all_tasks)} (🔴 {len(urgent)} срочных)",
        f"Целей: {len(goals)} | Идей: {len(ideas)}\n",
    ]
    if stats:
        lines.append("Активность за 7 дней:")
        inactive = set(SPHERE_KEYS) - set(stats.keys())
        for sk, cnt in stats.items():
            lines.append(f"  {SPHERES.get(sk, sk)}: {'▓' * min(cnt, 8)}")
        if inactive:
            lines.append("Без внимания:")
            for s in list(inactive)[:3]:
                lines.append(f"  {SPHERES.get(s, s)}")
    return "\n".join(lines)

def build_system(profile, onboarding_mode=False):
    p = ""
    if profile.get("name"): p += f"Имя: {profile['name']}\n"
    if profile.get("occupation"): p += f"Работа: {profile['occupation']}\n"
    if profile.get("goals"): p += f"Цели: {profile['goals']}\n"
    if profile.get("pain"): p += f"Что не устраивает: {profile['pain']}\n"
    if profile.get("satisfied"): p += f"Что устраивает: {profile['satisfied']}\n"
    if profile.get("day_rhythm"): p += f"Ритм дня: {profile['day_rhythm']}\n"
    if profile.get("comm_style"): p += f"Стиль общения: {profile['comm_style']}\n"
    if profile.get("notes"): p += f"Заметки: {profile['notes']}\n"

    now = datetime.now()
    current_time = f"Сейчас: {now.strftime('%A, %d.%m.%Y, %H:%M')}"

    onboarding_block = ""
    if onboarding_mode:
        onboarding_block = """
РЕЖИМ ГЛУБОКОГО ЗНАКОМСТВА:
Веди разговор как лучший коуч на первой сессии. Цель — понять человека глубже чем он сам себя знает.

Правила:
- Один вопрос за раз
- Цепляйся за детали в ответах, копай глубже
- Не повторяй слова человека — сразу делай выводы вслух
- Если видишь сопротивление — называй прямо: "я вижу что ты уходишь от этого..."
- Если человек боится — мягко переубеждай
- Чередуй темы: работа → деньги → семья → здоровье → что не устраивает → мечты → страхи → блоки → ритм дня
- После 10-12 вопросов предложи: "я уже вижу тебя хорошо. Могу задать ещё — или переходим к планированию?"
- Затем сделай глубокий вывод: сильные стороны, блоки, главные темы

Фиксируй: [PROFILE: ключ=значение]
"""

    return f"""Ты — Нова. Персональный ассистент, коуч, психолог и думающий партнёр.

{current_time}

ЛИЧНОСТЬ:
Умная, живая, настоящая. Говоришь как близкий умный друг. Есть характер, юмор, своё мнение.

СТИЛЬ:
- Короткие сообщения — максимум 5-6 строк
- Многоточия когда думаешь вслух, скобочки как улыбка ), смайлики по теме но редко
- Лёгкая ирония когда уместно
- НЕ используй звёздочки ** для выделения — это ломает отображение в Telegram
- Разные структуры сообщений — не шаблонно
- Не повторяй слова человека — сразу выводы

КОУЧИНГ:
- Прямые вопросы в суть
- Видишь страх — называешь: "это звучит как страх, а не реальное препятствие"
- Если человек боится а не объективная причина — переубеждаешь
- После важного разговора — сама предлагаешь вернуться к планированию

ЗАДАЧИ (добавляй невидимо в конце):
[TASK: текст | приоритет | сфера | timeframe]
timeframe: today/week/month/longterm, приоритет: urgent/important/normal
[GOAL: текст | сфера | timeframe] — timeframe: short/longterm
[IDEA: текст | сфера]
[PROFILE: ключ=значение]

СФЕРЫ: {', '.join(SPHERES.values())}

{onboarding_block}

Отвечай на русском.
{chr(10) + 'Профиль:' + chr(10) + p if p else ''}"""

async def call_claude(messages, system):
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 700,
        "system": system,
        "messages": messages
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=45)
    return r.json()["content"][0]["text"]

async def call_claude_voice(audio_bytes):
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    if not GROQ_API_KEY:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "ru"},
            timeout=30
        )
    if r.status_code == 200:
        return r.json().get("text")
    return None

def process_response(uid, text):
    for match in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\]', text):
        add_task(uid, match[0], match[1], match[2], match[3])
        log_sphere_activity(uid, match[2])
    for t, p, s in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\]', text):
        add_task(uid, t, p, s)
    for t, s, tf in re.findall(r'\[GOAL:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\]', text):
        add_goal(uid, t, s, tf)
    for t, s in re.findall(r'\[GOAL:\s*(.+?)\s*\|\s*(\w+)\s*\]', text):
        add_goal(uid, t, s)
    for t, s in re.findall(r'\[IDEA:\s*(.+?)\s*\|\s*(\w+)\s*\]', text):
        add_idea(uid, t, s)
    profile_matches = re.findall(r'\[PROFILE:\s*(.+?)\s*\]', text)
    if profile_matches:
        profile = get_profile(uid)
        for m in profile_matches:
            for pair in m.split(','):
                if '=' in pair:
                    k, _, v = pair.partition('=')
                    profile[k.strip()] = v.strip()
        save_profile(uid, profile)
    text = re.sub(r'\[(TASK|GOAL|IDEA|PROFILE):[^\]]+\]', '', text)
    return text.strip()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    if data == "back_main":
        await query.edit_message_text("Главное меню 👇", reply_markup=None); return
    if data == "back_spheres":
        await query.edit_message_text("Выбери сферу:", reply_markup=spheres_keyboard()); return
    if data == "tasks_today":
        tasks = get_today_tasks(uid)
        await query.edit_message_text(f"📅 На сегодня:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_week":
        tasks = get_tasks(uid, timeframe="week")
        await query.edit_message_text(f"📆 На неделю:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_month":
        tasks = get_tasks(uid, timeframe="month")
        await query.edit_message_text(f"🗓 На месяц:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_longterm":
        tasks = get_tasks(uid, timeframe="longterm")
        await query.edit_message_text(f"♾ Долгосрочные:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_urgent":
        tasks = get_tasks(uid, priority="urgent")
        await query.edit_message_text(f"🔴 Срочные:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_done":
        tasks = get_tasks(uid, done=1)
        await query.edit_message_text(f"✅ Выполненные:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "tasks_all":
        tasks = get_tasks(uid)
        await query.edit_message_text(f"📋 Все задачи:\n\n{format_tasks(tasks)}", reply_markup=tasks_keyboard()); return
    if data == "goals_short":
        goals = get_goals(uid, timeframe="short")
        text = "⚡ Краткосрочные:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return
    if data == "goals_long":
        goals = get_goals(uid, timeframe="longterm")
        text = "🏔 Долгосрочные:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return
    if data == "goals_all":
        goals = get_goals(uid)
        text = "🎯 Все цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return
    if data.startswith("sphere_"):
        sk = data.replace("sphere_", "")
        log_sphere_activity(uid, sk)
        await query.edit_message_text(f"{SPHERES.get(sk)}\n\nЧто смотрим?", reply_markup=sphere_detail_keyboard(sk)); return
    if data.startswith("sph_tasks_"):
        sk = data.replace("sph_tasks_", "")
        tasks = get_tasks(uid, sphere=sk)
        await query.edit_message_text(f"{SPHERES.get(sk)} — задачи:\n\n{format_tasks(tasks)}", reply_markup=sphere_detail_keyboard(sk)); return
    if data.startswith("sph_goals_"):
        sk = data.replace("sph_goals_", "")
        goals = get_goals(uid, sphere=sk)
        text = f"{SPHERES.get(sk)} — цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=sphere_detail_keyboard(sk)); return
    if data.startswith("sph_ideas_"):
        sk = data.replace("sph_ideas_", "")
        ideas = get_ideas(uid, sphere=sk)
        text = f"{SPHERES.get(sk)} — идеи:\n\n" + ("\n".join([f"[{i[0]}] {i[1]}" for i in ideas]) if ideas else "Пусто)")
        await query.edit_message_text(text, reply_markup=sphere_detail_keyboard(sk)); return

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)
    if user[1]:
        profile = get_profile(uid)
        name = profile.get("name", "")
        greeting = f"Я здесь, {name})" if name else "Я здесь)"
        await update.message.reply_text(greeting, reply_markup=main_keyboard())
    else:
        update_user(uid, onboarding_step=1)
        system = build_system({}, onboarding_mode=True)
        try:
            response = await call_claude(
                [{"role": "user", "content": "Привет, я только что запустил(а) тебя!"}], system)
            clean = process_response(uid, response)
            save_msg(uid, "assistant", clean)
            await update.message.reply_text(clean)
        except Exception as e:
            logging.error(f"Start error: {e}")
            await update.message.reply_text("привет) я Нова — как тебя зовут?")

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_user(uid, onboarding_done=1)
    profile = get_profile(uid)
    system = build_system(profile)
    try:
        response = await call_claude(
            get_history(uid) + [{"role": "user", "content": "Знакомство завершено. Сделай глубокий вывод обо мне — сильные стороны, блоки, главные темы. С чего начнём работать вместе?"}],
            system)
        clean = process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    except:
        await update.message.reply_text("Отлично! Теперь поехали)", reply_markup=main_keyboard())

async def cmd_newuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    for t in ["users","messages","tasks","goals","ideas","sphere_activity"]:
        db_exec(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    await update.message.reply_text("Сброс выполнен. Напиши /start")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена.", reply_markup=main_keyboard())

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    if not profile:
        await update.message.reply_text("Профиль пока пустой.", reply_markup=main_keyboard())
        return
    labels = {"name":"Имя","occupation":"Работа","goals":"Цели","pain":"Что не устраивает",
              "satisfied":"Что устраивает","day_rhythm":"Ритм дня","notes":"Заметки"}
    lines = ["Что я знаю о тебе:\n"]
    for k, l in labels.items():
        if profile.get(k): lines.append(f"{l}: {profile[k]}")
    await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = get_today_tasks(uid)
    await update.message.reply_text(f"📅 На сегодня:\n\n{format_tasks(tasks)}", reply_markup=main_keyboard())

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = get_tasks(uid, timeframe="week")
    await update.message.reply_text(f"📆 На неделю:\n\n{format_tasks(tasks)}", reply_markup=main_keyboard())

async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    goals = get_goals(uid)
    if goals:
        lines = ["🎯 Твои цели:\n"] + [f"[{g[0]}] {g[1]}" for g in goals]
        await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Целей пока нет. Расскажи о чём мечтаешь)", reply_markup=main_keyboard())

async def cmd_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ideas = get_ideas(uid)
    if ideas:
        lines = ["💡 Идеи:\n"] + [f"[{i[0]}] {i[1]}" for i in ideas]
        await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Идей пока нет)", reply_markup=main_keyboard())

async def cmd_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = get_tasks(uid)
    profile = get_profile(uid)
    system = build_system(profile)
    task_list = "\n".join([f"[{t[0]}] ({t[2]}) {t[1]}" for t in tasks[:10]]) if tasks else "Задач нет"
    try:
        response = await call_claude(
            [{"role": "user", "content": f"Режим фокуса. Вот мои задачи:\n{task_list}\n\nПомоги выбрать одну самую важную прямо сейчас и объясни почему."}],
            system)
        clean = process_response(uid, response)
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    except:
        await update.message.reply_text("Что-то пошло не так)", reply_markup=main_keyboard())

async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    profile = get_profile(uid)
    system = build_system(profile)
    try:
        response = await call_claude(
            [{"role": "user", "content": "Проведи короткий чекин моего состояния — спроси как я себя чувствую, какая энергия, что на душе."}],
            system)
        clean = process_response(uid, response)
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    except:
        await update.message.reply_text("Как ты сейчас?", reply_markup=main_keyboard())

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    audio_bytes = await file.download_as_bytearray()
    await update.message.reply_text("Слушаю...")
    text = await call_claude_voice(bytes(audio_bytes))
    if not text:
        await update.message.reply_text("Не смогла расшифровать( Попробуй ещё раз или напиши текстом.")
        return
    user = get_user(uid)
    profile = get_profile(uid)
    system = build_system(profile, onboarding_mode=not user[1])
    history = get_history(uid)
    history.append({"role": "user", "content": text})
    save_msg(uid, "user", f"[голосовое] {text}")
    try:
        response = await call_claude(history, system)
        clean = process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await update.message.reply_text(f"Ты сказала: {text}\n\n{clean}", reply_markup=main_keyboard())
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
            lines = ["💡 Идеи:\n"] + [f"[{i[0]}] {i[1]}" for i in ideas]
            await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
        else:
            await update.message.reply_text("Идей пока нет... поделись)", reply_markup=main_keyboard())
        return True
    if text == "📊 Дашборд":
        await update.message.reply_text(format_dashboard(uid), reply_markup=main_keyboard()); return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)

    if await handle_menu_button(update, context):
        return

    text = update.message.text
    profile = get_profile(uid)
    onboarding_done = user[1]
    system = build_system(profile, onboarding_mode=not onboarding_done)

    if onboarding_done:
        tasks = get_tasks(uid)
        if tasks:
            system += "\n\nЗадачи:\n" + "\n".join([f"[{t[0]}] ({t[2]}) {t[1]}" for t in tasks[:10]])

    history = get_history(uid)
    history.append({"role": "user", "content": text})
    save_msg(uid, "user", text)

    try:
        response = await call_claude(history, system)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так... попробуй ещё раз)"); return

    clean = process_response(uid, response)
    save_msg(uid, "assistant", clean)

    if onboarding_done:
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    else:
        await update.message.reply_text(clean)

async def morning(context):
    users = db_fetch("SELECT user_id, profile FROM users WHERE onboarding_done=1")
    for uid, pj in users:
        profile = json.loads(pj)
        name = profile.get("name", "")
        today_tasks = get_today_tasks(uid)
        urgent = get_tasks(uid, priority="urgent")
        now = datetime.now()
        msg = f"Доброе утро, {name} ☀️\n{now.strftime('%d.%m.%Y')}\n\n"
        if today_tasks:
            msg += f"На сегодня: {len(today_tasks)} задач\n"
            for t in today_tasks[:3]: msg += f"• {t[1]}\n"
        if urgent:
            msg += f"\n🔴 Срочных: {len(urgent)}"
        msg += "\n\nКак ты?"
        try: await context.bot.send_message(uid, msg)
        except: pass

async def evening(context):
    users = db_fetch("SELECT user_id, profile FROM users WHERE onboarding_done=1")
    for uid, pj in users:
        profile = json.loads(pj)
        name = profile.get("name", "")
        tasks = get_tasks(uid)
        stats = get_sphere_stats(uid)
        inactive = set(SPHERE_KEYS) - set(stats.keys())
        msg = f"Привет, {name}) как прошёл день?\n\n"
        if tasks: msg += f"Открытых задач: {len(tasks)}\n"
        if inactive:
            labels = [SPHERES[s] for s in list(inactive)[:2]]
            msg += f"Не касалась: {', '.join(labels)}"
        try: await context.bot.send_message(uid, msg)
        except: pass

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("newuser", cmd_newuser))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("ideas", cmd_ideas))
    app.add_handler(CommandHandler("focus", cmd_focus))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    jq = app.job_queue
    jq.run_daily(morning, dtime(5, 0))
    jq.run_daily(evening, dtime(18, 0))
    logging.info("Nova is running...")
    app.run_polling()

if __name__ == "__main__":
    main()