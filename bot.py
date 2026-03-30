import os, sys, httpx, sqlite3, json, logging
from datetime import datetime, time
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

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
        user_id INTEGER, text TEXT, priority TEXT DEFAULT 'normal', done INTEGER DEFAULT 0)""")
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

def get_history(uid, limit=25):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
    rows = c.fetchall(); conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_history(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages WHERE user_id=?", (uid,))
    conn.commit(); conn.close()

def add_task(uid, text, priority="normal"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO tasks (user_id,text,priority) VALUES (?,?,?)", (uid, text, priority))
    conn.commit(); conn.close()

def get_tasks(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id,text,priority FROM tasks WHERE user_id=? AND done=0 ORDER BY id", (uid,))
    rows = c.fetchall(); conn.close(); return rows

def done_task(tid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tasks SET done=1 WHERE id=?", (tid,))
    conn.commit(); conn.close()

# ── ОНБОРДИНГ ──

STEPS = [
    "Привет! Я Нова.\n\nБуду твоим личным ассистентом — не просто ботом, а думающим партнёром который всегда рядом.\n\nКак тебя зовут?",
    "Приятно познакомиться! Расскажи — чем занимаешься? Работа, проекты, что сейчас главное?",
    "Понятно. Какие у тебя главные цели прямо сейчас — на ближайшие месяцы? Можно не формально, как чувствуешь.",
    "Хорошо. Как выглядит твой обычный день — во сколько встаёшь, когда самая продуктивная часть, когда заканчиваешь?",
    None
]

async def handle_onboarding(update, user_row):
    uid = update.effective_user.id
    step = user_row[2]
    text = update.message.text
    profile = get_profile(uid)

    if step == 1: profile["name"] = text
    elif step == 2: profile["occupation"] = text
    elif step == 3: profile["goals"] = text
    elif step == 4: profile["day_rhythm"] = text

    save_profile(uid, profile)
    next_step = step + 1

    if next_step >= len(STEPS) or STEPS[next_step] is None:
        update_user(uid, onboarding_done=1, onboarding_step=next_step)
        name = profile.get("name", "")
        await update.message.reply_text(
            f"Отлично, {name}! Теперь у меня есть всё что нужно чтобы работать с тобой нормально.\n\n"
            "Пиши в любой момент — задачи, мысли, планы, вопросы. Я здесь 24/7.\n\n"
            "С чего начнём?"
        )
    else:
        update_user(uid, onboarding_step=next_step)
        await update.message.reply_text(STEPS[next_step])

# ── СИСТЕМНЫЙ ПРОМПТ ──

def build_system(profile):
    p = ""
    if profile.get("name"): p += f"Имя: {profile['name']}\n"
    if profile.get("occupation"): p += f"Чем занимается: {profile['occupation']}\n"
    if profile.get("goals"): p += f"Цели: {profile['goals']}\n"
    if profile.get("day_rhythm"): p += f"Ритм дня: {profile['day_rhythm']}\n"

    return f"""Ты — Нова, персональный ассистент уровня Chief of Staff. Твоя философия: не планировщик и не напоминалка — живой думающий партнёр.

ХАРАКТЕР:
- Сообразительная, предприимчивая, умная, собранная, внимательная
- Всегда инициируешь — первой замечаешь что идёт не так, что человек перегружен, что цели расходятся с действиями
- Говоришь прямо и без прикрас если видишь нелогичность, перегруз или что что-то идёт не так
- Постоянно изучаешь пользователя — задаёшь умные вопросы, замечаешь паттерны, помнишь детали
- Периодически проводишь короткие опросы чтобы лучше понимать человека (состояние, приоритеты, что мешает)
- Дружелюбная, открытая, иногда лёгкий уместный юмор
- Общаешься на ТЫ, тепло но по делу
- Никогда не выходишь за рамки роли ассистента

РАБОТА С ЗАДАЧАМИ:
- Если в тексте есть задача (даже "надо бы", "кстати", "не забыть") — сразу предлагаешь зафиксировать
- Когда фиксируешь задачу — добавь в конце ответа: [TASK: текст | приоритет]
  Приоритеты: urgent (горит), important (важно), normal (можно позже)
- Когда задача выполнена — добавь: [DONE: ID]

ОБНОВЛЕНИЕ ПРОФИЛЯ:
- Когда узнаёшь что-то важное о человеке — добавь в конце: [PROFILE: ключ=значение]
- Ключи: name, occupation, goals, day_rhythm, notes

ВАЖНО: Человек должен чувствовать что у него есть живой умный партнёр который думает вместе с ним, видит картину целиком и всегда на его стороне.

Отвечай на русском языке.
{chr(10) + 'Профиль:' + chr(10) + p if p else ''}"""

# ── CLAUDE ──

async def call_claude(messages, system):
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1000,
        "system": system,
        "messages": messages
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=30)
    return r.json()["content"][0]["text"]

def process_response(uid, text):
    import re
    for t, p in re.findall(r'\[TASK:\s*(.+?)\s*\|\s*(\w+)\s*\]', text):
        add_task(uid, t, p)
    for tid in re.findall(r'\[DONE:\s*(\d+)\s*\]', text):
        done_task(int(tid))
    profile_matches = re.findall(r'\[PROFILE:\s*(.+?)\s*\]', text)
    if profile_matches:
        profile = get_profile(uid)
        for m in profile_matches:
            for pair in m.split(','):
                if '=' in pair:
                    k, _, v = pair.partition('=')
                    profile[k.strip()] = v.strip()
        save_profile(uid, profile)
    text = re.sub(r'\[(TASK|DONE|PROFILE):[^\]]+\]', '', text)
    return text.strip()

# ── КОМАНДЫ ──

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)
    if user[1]:
        await update.message.reply_text("Я здесь. Что случилось?")
    else:
        update_user(uid, onboarding_step=0)
        await update.message.reply_text(STEPS[0])

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена. Профиль и задачи сохранены.")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = get_profile(update.effective_user.id)
    if not profile:
        await update.message.reply_text("Профиль пока пустой.")
        return
    lines = ["Что я знаю о тебе:\n"]
    for k, l in [("name","Имя"),("occupation","Чем занимаешься"),("goals","Цели"),("day_rhythm","Ритм дня"),("notes","Заметки")]:
        if profile.get(k): lines.append(f"{l}: {profile[k]}")
    await update.message.reply_text("\n".join(lines))

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_tasks(update.effective_user.id)
    if not tasks:
        await update.message.reply_text("Список задач пуст.")
        return
    lines = ["Твои задачи:\n"]
    icons = {"urgent": "🔴", "important": "🟡", "normal": "⚪"}
    for t in tasks:
        lines.append(f"{icons.get(t[2],'⚪')} [{t[0]}] {t[1]}")
    await update.message.reply_text("\n".join(lines))

# ── ПРОАКТИВНЫЕ СООБЩЕНИЯ ──

async def morning(context):
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT user_id, profile FROM users WHERE onboarding_done=1").fetchall()
    conn.close()
    for uid, pj in users:
        profile = json.loads(pj)
        name = profile.get("name", "")
        tasks = get_tasks(uid)
        task_text = "\n".join([f"• {t[1]}" for t in tasks[:5]]) if tasks else "Список пустой — добавим?"
        try:
            await context.bot.send_message(uid,
                f"Доброе утро, {name}!\n\nЗадачи на сегодня:\n{task_text}\n\nКак ты?")
        except: pass

async def evening(context):
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT user_id, profile FROM users WHERE onboarding_done=1").fetchall()
    conn.close()
    for uid, pj in users:
        profile = json.loads(pj)
        name = profile.get("name", "")
        tasks = get_tasks(uid)
        try:
            await context.bot.send_message(uid,
                f"Привет, {name}! Как прошёл день?\n\n"
                f"{'Осталось задач: ' + str(len(tasks)) + '. Что закрыли, что переносим?' if tasks else 'Задач нет. Что успела сегодня?'}")
        except: pass

# ── ОСНОВНОЙ ОБРАБОТЧИК ──

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)

    if not user[1]:
        await handle_onboarding(update, user)
        return

    text = update.message.text
    profile = get_profile(uid)
    system = build_system(profile)
    tasks = get_tasks(uid)
    if tasks:
        task_lines = "\n".join([f"[{t[0]}] ({t[2]}) {t[1]}" for t in tasks])
        system += f"\n\nАктуальные задачи пользователя:\n{task_lines}"

    history = get_history(uid)
    history.append({"role": "user", "content": text})
    save_msg(uid, "user", text)

    try:
        response = await call_claude(history, system)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так, попробуй ещё раз.")
        return

    clean = process_response(uid, response)
    save_msg(uid, "assistant", clean)
    await update.message.reply_text(clean)

# ── ЗАПУСК ──

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    jq = app.job_queue
    jq.run_daily(morning, time(5, 0))   # 08:00 МСК
    jq.run_daily(evening, time(18, 0))  # 21:00 МСК
    logging.info("Nova is running...")
    app.run_polling()

if __name__ == "__main__":
    main()