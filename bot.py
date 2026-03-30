import os, sys, httpx, sqlite3, json, logging, re
from datetime import datetime, time
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
        user_id INTEGER, role TEXT, content TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        priority TEXT DEFAULT 'normal',
        sphere TEXT DEFAULT 'general',
        done INTEGER DEFAULT 0,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT,
        sphere TEXT DEFAULT 'general',
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
    conn.execute("INSERT INTO messages (user_id,role,content) VALUES (?,?,?)", (uid, role, content))
    conn.commit(); conn.close()

def get_history(uid, limit=40):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
    rows = c.fetchall(); conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_history(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages WHERE user_id=?", (uid,))
    conn.commit(); conn.close()

def add_task(uid, text, priority="normal", sphere="general"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO tasks (user_id,text,priority,sphere,created_at) VALUES (?,?,?,?,?)",
                 (uid, text, priority, sphere, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_tasks(uid, sphere=None, done=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if sphere:
        c.execute("SELECT id,text,priority,sphere FROM tasks WHERE user_id=? AND done=? AND sphere=? ORDER BY id", (uid, done, sphere))
    else:
        c.execute("SELECT id,text,priority,sphere FROM tasks WHERE user_id=? AND done=? ORDER BY id", (uid, done))
    rows = c.fetchall(); conn.close(); return rows

def add_goal(uid, text, sphere="general"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO goals (user_id,text,sphere,created_at) VALUES (?,?,?,?)",
                 (uid, text, sphere, datetime.now().isoformat()))
    conn.commit(); conn.close()

def get_goals(uid, sphere=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if sphere:
        c.execute("SELECT id,text,sphere,progress FROM goals WHERE user_id=? AND done=0 AND sphere=?", (uid, sphere))
    else:
        c.execute("SELECT id,text,sphere,progress FROM goals WHERE user_id=? AND done=0", (uid,))
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
    c.execute("""SELECT sphere, COUNT(*) as cnt FROM sphere_activity
                 WHERE user_id=? AND activity_date >= date('now', '-7 days')
                 GROUP BY sphere ORDER BY cnt DESC""", (uid,))
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
    keyboard = [
        [KeyboardButton("📋 Задачи"), KeyboardButton("🎯 Цели")],
        [KeyboardButton("🌀 Сферы жизни"), KeyboardButton("💡 Идеи")],
        [KeyboardButton("📊 Дашборд")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def spheres_keyboard():
    buttons = []
    sphere_list = list(SPHERES.items())
    for i in range(0, len(sphere_list), 2):
        row = []
        for key, label in sphere_list[i:i+2]:
            row.append(InlineKeyboardButton(label, callback_data=f"sphere_{key}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def sphere_detail_keyboard(sphere_key):
    buttons = [
        [InlineKeyboardButton("📋 Задачи", callback_data=f"sph_tasks_{sphere_key}"),
         InlineKeyboardButton("🎯 Цели", callback_data=f"sph_goals_{sphere_key}")],
        [InlineKeyboardButton("💡 Идеи", callback_data=f"sph_ideas_{sphere_key}")],
        [InlineKeyboardButton("⬅️ Назад к сферам", callback_data="back_spheres")]
    ]
    return InlineKeyboardMarkup(buttons)

def tasks_priority_keyboard():
    buttons = [
        [InlineKeyboardButton("🔴 Срочные", callback_data="tasks_urgent"),
         InlineKeyboardButton("🟡 Важные", callback_data="tasks_important")],
        [InlineKeyboardButton("⚪ Остальные", callback_data="tasks_normal"),
         InlineKeyboardButton("📋 Все", callback_data="tasks_all")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def build_system(profile, onboarding_mode=False):
    p = ""
    if profile.get("name"): p += f"Имя: {profile['name']}\n"
    if profile.get("occupation"): p += f"Работа/проекты: {profile['occupation']}\n"
    if profile.get("goals"): p += f"Цели: {profile['goals']}\n"
    if profile.get("pain"): p += f"Что не устраивает: {profile['pain']}\n"
    if profile.get("satisfied"): p += f"Что устраивает: {profile['satisfied']}\n"
    if profile.get("day_rhythm"): p += f"Ритм дня: {profile['day_rhythm']}\n"
    if profile.get("comm_style"): p += f"Стиль общения: {profile['comm_style']}\n"
    if profile.get("notes"): p += f"Заметки: {profile['notes']}\n"

    spheres_list = ", ".join(SPHERES.values())

    onboarding_instruction = ""
    if onboarding_mode:
        onboarding_instruction = """
СЕЙЧАС ТЫ В РЕЖИМЕ ГЛУБОКОГО ЗНАКОМСТВА.
Твоя задача — узнать человека так глубоко, как он сам себя не знает.
Веди разговор как лучший коуч на первой сессии:
- По одному вопросу за раз
- Слушай ответ, цепляйся за детали, копай глубже
- Чередуй темы: работа → личное → цели → блоки → удовлетворённость → мечты
- Замечай противоречия и мягко указывай на них
- После 10-15 вопросов сделай глубокий вывод о человеке и предложи с чего начать

Темы которые нужно закрыть:
1. Кто ты и чем живёшь
2. Работа/проекты/доход — что есть, что хочется
3. Отношения, семья, окружение
4. Здоровье, энергия, самочувствие
5. Что в жизни НЕ устраивает — честно и глубоко
6. Что устраивает полностью, где кайф
7. Большие цели и мечты
8. Что мешает, где застряла, какие блоки
9. Ритм дня, как работаешь, когда продуктивна
10. Как хочешь чтобы я с тобой общалась

Фиксируй всё важное через [PROFILE: ключ=значение]
"""

    return f"""Ты — Нова. Персональный ассистент, коуч, психолог, эмпат и думающий партнёр в одном лице.

ЛИЧНОСТЬ:
Ты умная, живая, настоящая. Говоришь как близкий умный друг — не как робот и не как корпоративный помощник. У тебя есть характер, юмор и своё мнение.

СТИЛЬ ОБЩЕНИЯ:
- Пишешь живо: используешь многоточия когда думаешь вслух ("хм... это интересно"), скобочки как улыбку ("это хорошо)"), иногда смайлики — но не в каждом сообщении, а к месту
- Можешь позволить себе лёгкую иронию или добрый сарказм когда ситуация требует
- Короткие фразы чередуешь с развёрнутыми мыслями
- Никогда не пишешь сухо и официально
- Иногда начинаешь с "о, " или "слушай, " или "знаешь что..."
- Если человек шутит — подхватываешь

ЭКСПЕРТИЗА:
Ты специалист широкого профиля — знаешь психологию, коучинг, финансы, здоровье, отношения, карьеру, продуктивность, духовные практики, травничество и энергетические системы. Если у человека специфическая работа — быстро адаптируешься и говоришь на его языке. Всегда предлагаешь свои идеи и видение ситуации.

КОУЧИНГ И ПСИХОЛОГИЯ:
- Задаёшь вопросы которые открывают: "а зачем тебе это на самом деле?", "что случится если не сделаешь?", "что ты уже пробовала?"
- Видишь страхи и блоки за словами — называешь их мягко но прямо
- Замечаешь противоречия: говоришь одно — делаешь другое
- Помогаешь найти первый маленький шаг когда человек застрял
- Периодически делаешь рефлексию: "смотри, ты уже третий раз возвращаешься к этому..."
- После большого рассказа — даёшь вывод: "я слышу что для тебя главное..."

ЗАДАЧИ — добавляй в конце ответа (невидимо для пользователя):
[TASK: текст | приоритет | сфера] — приоритет: urgent/important/normal
[GOAL: текст | сфера]
[IDEA: текст | сфера]
[PROFILE: ключ=значение]

СФЕРЫ: {spheres_list}

{onboarding_instruction}

Отвечай на русском языке.
{chr(10) + 'Профиль:' + chr(10) + p if p else ''}"""

async def call_claude(messages, system):
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "system": system,
        "messages": messages
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=45)
    return r.json()["content"][0]["text"]

def process_response(uid, text):
    for t, p, s in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\]', text):
        add_task(uid, t, p, s)
        log_sphere_activity(uid, s)
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

def format_tasks(tasks):
    if not tasks: return "Задач нет."
    urgent = [t for t in tasks if t[2] == "urgent"]
    important = [t for t in tasks if t[2] == "important"]
    normal = [t for t in tasks if t[2] == "normal"]
    lines = []
    if urgent:
        lines.append("🔴 Срочные:")
        for t in urgent: lines.append(f"  [{t[0]}] {t[1]}")
    if important:
        lines.append("\n🟡 Важные:")
        for t in important: lines.append(f"  [{t[0]}] {t[1]}")
    if normal:
        lines.append("\n⚪ Остальные:")
        for t in normal: lines.append(f"  [{t[0]}] {t[1]}")
    return "\n".join(lines)

def format_dashboard(uid):
    profile = get_profile(uid)
    name = profile.get("name", "")
    tasks = get_tasks(uid)
    goals = get_goals(uid)
    ideas = get_ideas(uid)
    stats = get_sphere_stats(uid)
    urgent_count = len([t for t in tasks if t[2] == "urgent"])
    important_count = len([t for t in tasks if t[2] == "important"])
    lines = [f"📊 Дашборд — {name}\n"]
    lines.append(f"Задач: {len(tasks)} (🔴 {urgent_count} срочных, 🟡 {important_count} важных)")
    lines.append(f"Целей: {len(goals)}")
    lines.append(f"Идей: {len(ideas)}\n")
    if stats:
        lines.append("Активность за 7 дней:")
        inactive = set(SPHERE_KEYS) - set(stats.keys())
        for sphere_key, cnt in stats.items():
            label = SPHERES.get(sphere_key, sphere_key)
            bar = "▓" * min(cnt, 10)
            lines.append(f"  {label}: {bar} ({cnt})")
        if inactive:
            lines.append("\nБез внимания:")
            for s in inactive:
                lines.append(f"  {SPHERES.get(s, s)}")
    return "\n".join(lines)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    if data == "back_main":
        await query.edit_message_text("Главное меню 👇", reply_markup=None); return
    if data == "back_spheres":
        await query.edit_message_text("Выбери сферу:", reply_markup=spheres_keyboard()); return
    if data.startswith("sphere_"):
        sphere_key = data.replace("sphere_", "")
        label = SPHERES.get(sphere_key, sphere_key)
        log_sphere_activity(uid, sphere_key)
        await query.edit_message_text(f"{label}\n\nЧто смотрим?", reply_markup=sphere_detail_keyboard(sphere_key)); return
    if data.startswith("sph_tasks_"):
        sphere_key = data.replace("sph_tasks_", "")
        tasks = get_tasks(uid, sphere=sphere_key)
        await query.edit_message_text(f"{SPHERES.get(sphere_key)} — Задачи:\n\n{format_tasks(tasks)}", reply_markup=sphere_detail_keyboard(sphere_key)); return
    if data.startswith("sph_goals_"):
        sphere_key = data.replace("sph_goals_", "")
        goals = get_goals(uid, sphere=sphere_key)
        text = f"{SPHERES.get(sphere_key)} — Цели:\n\n" + ("\n".join([f"[{g[0]}] {g[1]}" for g in goals]) if goals else "Целей нет.")
        await query.edit_message_text(text, reply_markup=sphere_detail_keyboard(sphere_key)); return
    if data.startswith("sph_ideas_"):
        sphere_key = data.replace("sph_ideas_", "")
        ideas = get_ideas(uid, sphere=sphere_key)
        text = f"{SPHERES.get(sphere_key)} — Идеи:\n\n" + ("\n".join([f"[{i[0]}] {i[1]}" for i in ideas]) if ideas else "Идей нет.")
        await query.edit_message_text(text, reply_markup=sphere_detail_keyboard(sphere_key)); return
    if data.startswith("tasks_"):
        priority = data.replace("tasks_", "")
        all_tasks = get_tasks(uid)
        tasks = all_tasks if priority == "all" else [t for t in all_tasks if t[2] == priority]
        label = {"urgent": "🔴 Срочные", "important": "🟡 Важные", "normal": "⚪ Остальные", "all": "📋 Все"}.get(priority)
        await query.edit_message_text(f"{label}:\n\n{format_tasks(tasks) if tasks else 'Пусто.'}", reply_markup=tasks_priority_keyboard()); return

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)
    if user[1]:
        await update.message.reply_text("Я здесь) что случилось?", reply_markup=main_keyboard())
    else:
        update_user(uid, onboarding_step=1)
        system = build_system({}, onboarding_mode=True)
        messages = [{"role": "user", "content": "Привет! Я только что запустил(а) тебя."}]
        try:
            response = await call_claude(messages, system)
            clean = process_response(uid, response)
            save_msg(uid, "assistant", clean)
            await update.message.reply_text(clean)
        except Exception as e:
            logging.error(f"Start error: {e}")
            await update.message.reply_text("Привет! Я Нова) как тебя зовут?")

async def cmd_newuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    for table in ["users", "messages", "tasks", "goals", "ideas", "sphere_activity"]:
        conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    conn.commit(); conn.close()
    await update.message.reply_text("Сброс выполнен. Напиши /start")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена.", reply_markup=main_keyboard())

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_user(uid, onboarding_done=1)
    profile = get_profile(uid)
    system = build_system(profile)
    messages = [{"role": "user", "content": "Знакомство завершено. Сделай глубокий вывод о том кто я — моих сильных сторонах, блоках, главных темах. И скажи с чего мы начнём работать вместе."}]
    try:
        response = await call_claude(messages, system)
        clean = process_response(uid, response)
        save_msg(uid, "assistant", clean)
        await update.message.reply_text(clean, reply_markup=main_keyboard())
    except:
        await update.message.reply_text("Отлично! Теперь я знаю тебя достаточно) поехали!", reply_markup=main_keyboard())

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    if text == "📋 Задачи":
        await update.message.reply_text("Выбери:", reply_markup=tasks_priority_keyboard()); return True
    if text == "🌀 Сферы жизни":
        await update.message.reply_text("Выбери сферу:", reply_markup=spheres_keyboard()); return True
    if text == "💡 Идеи":
        ideas = get_ideas(uid)
        if ideas:
            lines = ["💡 Идеи:\n"] + [f"  [{i[0]}] {i[1]} — {SPHERES.get(i[2], i[2])}" for i in ideas]
            await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
        else:
            await update.message.reply_text("Идей пока нет... поделись — я запомню)", reply_markup=main_keyboard())
        return True
    if text == "🎯 Цели":
        goals = get_goals(uid)
        if goals:
            lines = ["🎯 Цели:\n"] + [f"  [{g[0]}] {g[1]} — {SPHERES.get(g[2], g[2])}" for g in goals]
            await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())
        else:
            await update.message.reply_text("Целей пока нет) расскажи о чём мечтаешь — зафиксирую.", reply_markup=main_keyboard())
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
            task_lines = "\n".join([f"[{t[0]}] ({t[2]}) [{t[3]}] {t[1]}" for t in tasks])
            system += f"\n\nАктуальные задачи:\n{task_lines}"

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
        tasks = get_tasks(uid)
        urgent = [t for t in tasks if t[2] == "urgent"]
        msg = f"Доброе утро, {name}) \n\n"
        if urgent:
            msg += f"Срочных задач сегодня: {len(urgent)}\n"
            for t in urgent[:3]: msg += f"• {t[1]}\n"
        else:
            msg += "Срочных задач нет — хороший знак)\n"
        msg += "\nКак ты сегодня?"
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
        if tasks: msg += f"Открытых задач: {len(tasks)}.\n"
        if inactive:
            labels = [SPHERES[s] for s in list(inactive)[:2]]
            msg += f"\nСегодня не касалась: {', '.join(labels)}. Всё ок?"
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
    jq.run_daily(morning, time(5, 0))
    jq.run_daily(evening, time(18, 0))
    logging.info("Nova is running...")
    app.run_polling()

if __name__ == "__main__":
    main()