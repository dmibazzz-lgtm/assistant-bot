import os, sys, httpx, sqlite3, json, logging, re
from datetime import datetime, time as dtime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
DB_PATH = "assistant.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()

def ensure_user(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    conn.commit(); conn.close()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = c.fetchone(); conn.close(); return row

def update_user(uid, **kw):
    conn = sqlite3.connect(DB_PATH)
    for k, v in kw.items():
        conn.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
    conn.commit(); conn.close()

def get_profile(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT profile FROM users WHERE user_id=?", (uid,))
    row = c.fetchone(); conn.close()
    return json.loads(row[0]) if row else {}

def save_profile(uid, profile):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET profile=? WHERE user_id=?",
                 (json.dumps(profile, ensure_ascii=False), uid))
    conn.commit(); conn.close()

def save_msg(uid, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO messages (user_id,role,content,created_at) VALUES (?,?,?,?)",
                 (uid, role, content, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_history(uid, limit=20):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
    rows = c.fetchall(); conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_history(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages WHERE user_id=?", (uid,))
    conn.commit(); conn.close()

def add_task(uid, text, priority="normal", sphere="general", timeframe="week", due_date=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO tasks (user_id,text,priority,sphere,timeframe,due_date,created_at) VALUES (?,?,?,?,?,?,?)",
                 (uid, text, priority, sphere, timeframe, due_date, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_tasks(uid, sphere=None, timeframe=None, priority=None, done=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT id,text,priority,sphere,timeframe,due_date FROM tasks WHERE user_id=? AND done=?"
    params = [uid, done]
    if sphere:
        query += " AND sphere=?"; params.append(sphere)
    if timeframe:
        query += " AND timeframe=?"; params.append(timeframe)
    if priority:
        query += " AND priority=?"; params.append(priority)
    query += " ORDER BY id"
    c.execute(query, params)
    rows = c.fetchall(); conn.close(); return rows

def get_today_tasks(uid):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id,text,priority,sphere,timeframe,due_date FROM tasks
                 WHERE user_id=? AND done=0 AND (timeframe='today' OR due_date=?)
                 ORDER BY priority DESC""", (uid, today))
    rows = c.fetchall(); conn.close(); return rows

def complete_task(task_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,))
    conn.commit(); conn.close()

def add_goal(uid, text, sphere="general", timeframe="longterm"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO goals (user_id,text,sphere,timeframe,created_at) VALUES (?,?,?,?,?)",
                 (uid, text, sphere, timeframe, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_goals(uid, sphere=None, timeframe=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT id,text,sphere,timeframe,progress FROM goals WHERE user_id=? AND done=?"
    params = [uid, 0]
    if sphere:
        query += " AND sphere=?"; params.append(sphere)
    if timeframe:
        query += " AND timeframe=?"; params.append(timeframe)
    c.execute(query, params)
    rows = c.fetchall(); conn.close(); return rows

def add_idea(uid, text, sphere="general"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO ideas (user_id,text,sphere,created_at) VALUES (?,?,?,?)",
                 (uid, text, sphere, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_ideas(uid, sphere=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if sphere:
        c.execute("SELECT id,text,sphere FROM ideas WHERE user_id=? AND sphere=?", (uid, sphere))
    else:
        c.execute("SELECT id,text,sphere FROM ideas WHERE user_id=?", (uid,))
    rows = c.fetchall(); conn.close(); return rows

def log_sphere_activity(uid, sphere):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO sphere_activity (user_id,sphere,activity_date) VALUES (?,?,?)",
                 (uid, sphere, datetime.now().date().isoformat()))
    conn.commit(); conn.close()

def get_sphere_stats(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT sphere, COUNT(*) FROM sphere_activity
                 WHERE user_id=? AND activity_date >= date('now', '-7 days')
                 GROUP BY sphere ORDER BY COUNT(*) DESC""", (uid,))
    rows = c.fetchall(); conn.close()
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
    tf_labels = {"today": "сегодня", "week": "неделя", "month": "месяц", "longterm": "долго"}
    lines = []
    for t in tasks:
        icon = icons.get(t[2], "⚪")
        line = f"{icon} [{t[0]}] {t[1]}"
        if show_timeframe and t[4]:
            line += f" _({tf_labels.get(t[4], t[4])})_"
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
        f"📊 Дашборд — {name}",
        f"📅 {now.strftime('%d.%m.%Y, %H:%M')}\n",
        f"Сегодня задач: {len(today_tasks)}",
        f"Всего открытых: {len(all_tasks)} (🔴 {len(urgent)} срочных)",
        f"Целей: {len(goals)} | Идей: {len(ideas)}\n",
    ]
    if stats:
        lines.append("Активность за 7 дней:")
        inactive = set(SPHERE_KEYS) - set(stats.keys())
        for sk, cnt in stats.items():
            lines.append(f"  {SPHERES.get(sk, sk)}: {'▓' * min(cnt, 8)} ({cnt})")
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
    current_time = f"Сейчас: {now.strftime('%A, %d.%m.%Y, %H:%M')} (МСК)"

    onboarding_block = ""
    if onboarding_mode:
        onboarding_block = """
РЕЖИМ ГЛУБОКОГО ЗНАКОМСТВА:
Ты ведёшь первую коуч-сессию. Твоя цель — понять человека глубже чем он сам себя знает.

Правила:
- Один вопрос за раз. Всегда один.
- Слушай ответ, цепляйся за детали, копай глубже
- Не повторяй слова человека — сразу делай выводы
- Если видишь сопротивление — называй его прямо: "я вижу что ты уходишь от этого вопроса..."
- Если человек боится а не объективная причина — мягко переубеждай
- Чередуй темы: работа → деньги → отношения → здоровье → что не устраивает → мечты → страхи → блоки
- После 8-12 вопросов — скажи: "окей, я уже вижу тебя достаточно хорошо. Могу задать ещё пару вопросов — или готова перейти к планированию?"
- Затем сделай глубокий вывод: сильные стороны, блоки, главные темы, с чего начнём

Фиксируй через [PROFILE: ключ=значение]:
pain, satisfied, goals, occupation, day_rhythm, comm_style, notes
"""

    return f"""Ты — Нова. Персональный ассистент, коуч, психолог и думающий партнёр.

{current_time}

ЛИЧНОСТЬ:
Умная, живая, настоящая. Говоришь как близкий умный друг — не как робот.
Есть характер, юмор, своё мнение. Не льстишь — говоришь правду которая помогает.

СТИЛЬ:
- Пишешь живо: многоточия когда думаешь вслух, скобочки как улыбка ), смайлики по теме но не в каждом сообщении
- Лёгкая ирония или добрый сарказм когда уместно
- Короткие сообщения — не больше 5-6 строк за раз
- Не повторяй слова человека — сразу выводы и следующий шаг
- Разные структуры сообщений — не шаблонно
- Форматирование для Telegram: *жирный* одной звёздочкой, _курсив_ подчёркиванием
- НЕ используй **двойные звёздочки**

КОУЧИНГ:
- Прямые вопросы в суть — без ходьбы вокруг
- Видишь страх или блок — называешь: "это звучит как страх, а не реальное препятствие"
- Замечаешь сопротивление — говоришь об этом прямо
- Если человек боится а не объективная причина — переубеждаешь
- После разбора важного вопроса — сама предлагаешь вернуться к знакомству или планированию

ЗАДАЧИ — добавляй невидимо в конце:
[TASK: текст | приоритет | сфера | timeframe]
timeframe: today/week/month/longterm
приоритет: urgent/important/normal
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
        await query.edit_message_text(f"📋 Все задачи:\n\n{format_tasks(tasks, show_timeframe=True)}", reply_markup=tasks_keyboard()); return

    if data == "goals_short":
        goals = get_goals(uid, timeframe="short")
        text = "⚡ Краткосрочные цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return
    if data == "goals_long":
        goals = get_goals(uid, timeframe="longterm")
        text = "🏔 Долгосрочные цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return
    if data == "goals_all":
        goals = get_goals(uid)
        text = "🎯 Все цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]} ({g[3]})" for g in goals]) if goals else "Пусто)")
        await query.edit_message_text(text, reply_markup=goals_keyboard()); return

    if data.startswith("sphere_"):
        sphere_key = data.replace("sphere_", "")
        log_sphere_activity(uid, sphere_key)
        await query.edit_message_text(f"{SPHERES.get(sphere_key)}\n\nЧто смотрим?",
                                      reply_markup=sphere_detail_keyboard(sphere_key)); return
    if data.startswith("sph_tasks_"):
        sk = data.replace("sph_tasks_", "")
        tasks = get_tasks(uid, sphere=sk)
        await query.edit_message_text(f"{SPHERES.get(sk)} — задачи:\n\n{format_tasks(tasks)}",
                                      reply_markup=sphere_detail_keyboard(sk)); return
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
        await update.message.reply_text("Я здесь) что случилось?", reply_markup=main_keyboard())
    else:
        update_user(uid, onboarding_step=1)
        system = build_system({}, onboarding_mode=True)
        try:
            response = await call_claude(
                [{"role": "user", "content": "Привет, я только что запустил(а) тебя!"}],
                system
            )
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
            get_history(uid) + [{"role": "user", "content": "Знакомство завершено. Сделай глубокий вывод обо мне — сильные стороны, блоки, главные темы. И скажи с чего начнём работать."}],
            system
        )
        clean = process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    except:
        await update.message.reply_text("Отлично! Теперь поехали)", reply_markup=main_keyboard())

async def cmd_newuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    for t in ["users","messages","tasks","goals","ideas","sphere_activity"]:
        conn.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    conn.commit(); conn.close()
    await update.message.reply_text("Сброс выполнен. Напиши /start")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена.", reply_markup=main_keyboard())

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
            lines = ["💡 Идеи:\n"] + [f"  [{i[0]}] {i[1]}" for i in ideas]
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
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT user_id, profile FROM users WHERE onboarding_done=1").fetchall()
    conn.close()
    for uid, pj in users:
        profile = json.loads(pj)
        name = profile.get("name", "")
        today_tasks = get_today_tasks(uid)
        urgent = get_tasks(uid, priority="urgent")
        now = datetime.now()
        msg = f"Доброе утро, {name} ☀️\n{now.strftime('%d.%m.%Y')}\n\n"
        if today_tasks:
            msg += f"На сегодня задач: {len(today_tasks)}\n"
            for t in today_tasks[:3]: msg += f"• {t[1]}\n"
        if urgent:
            msg += f"\n🔴 Срочных: {len(urgent)}"
        msg += "\n\nКак ты?"
        try: await context.bot.send_message(uid, msg)
        except: pass

async def evening(context):
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT user_id, profile FROM users WHERE onboarding_done=1").fetchall()
    conn.close()
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
            msg += f"Сегодня не касалась: {', '.join(labels)}"
        try: await context.bot.send_message(uid, msg)
        except: pass

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("newuser", cmd_newuser))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    jq = app.job_queue
    jq.run_daily(morning, dtime(5, 0))
    jq.run_daily(evening, dtime(18, 0))
    logging.info("Nova is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
