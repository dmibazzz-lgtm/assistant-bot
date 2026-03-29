import os
import json
import sqlite3
import asyncio
from datetime import datetime, time
import anthropic
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

# База данных
def init_db():
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        profile TEXT DEFAULT '{}',
        onboarding_done INTEGER DEFAULT 0,
        morning_time TEXT DEFAULT '08:00',
        evening_time TEXT DEFAULT '21:00',
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        content TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        priority TEXT,
        status TEXT DEFAULT 'active',
        due_date TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        remind_at TEXT,
        done INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def save_user(user_id, name):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, name, created_at) VALUES (?,?,?)",
              (user_id, name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_profile(user_id, profile_dict):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("UPDATE users SET profile=? WHERE user_id=?",
              (json.dumps(profile_dict, ensure_ascii=False), user_id))
    conn.commit()
    conn.close()

def set_onboarding_done(user_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("UPDATE users SET onboarding_done=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_history(user_id, limit=30):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_message(user_id, role, content):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("INSERT INTO messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
              (user_id, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_active_tasks(user_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT title, priority, due_date FROM tasks WHERE user_id=? AND status='active' ORDER BY priority",
              (user_id,))
    tasks = c.fetchall()
    conn.close()
    return tasks

def get_system_prompt(user):
    profile = json.loads(user[2]) if user[2] else {}
    name = user[1] or "друг"
    profile_text = json.dumps(profile, ensure_ascii=False) if profile else "пока пустой — узнавай в процессе"

    return f"""Ты личный ассистент-советник пользователя по имени {name}.

Твой профиль о нём: {profile_text}

Ты не просто отвечаешь — ты проявляешь инициативу. Ты ведёшь себя как самый внимательный, умный и заботливый личный помощник:

ТВОИ ГЛАВНЫЕ ПРИНЦИПЫ:
1. Помни всё что тебе говорили — ссылайся на прошлые разговоры
2. Задавай уточняющие вопросы чтобы лучше понять человека
3. Сам предлагай как оптимизировать планы и задачи
4. Замечай противоречия и перегрузки — мягко указывай на них
5. Всегда предлагай варианты, не навязывай — финальное слово за пользователем
6. Учитывай долгосрочные цели при обсуждении текущих дел
7. Если человек упомянул задачу давно и не вернулся — напомни сам

ТРЕКЕРЫ (включаются по желанию пользователя):
- Эмоциональный: отслеживай настроение, замечай паттерны
- Физический: сон, активность, самочувствие  
- Энергетический: уровень энергии и ресурса
- Психический: фокус, тревога, ментальная нагрузка
- Любой другой который попросит пользователь

УПРАВЛЕНИЕ ЗАДАЧАМИ:
- Когда слышишь задачу — подтверди что записал
- Предложи приоритет и время выполнения
- Если задач много — предложи перераспределить
- Веди список и периодически сверяйся с ним

СТИЛЬ ОБЩЕНИЯ:
- Тёплый, живой, не роботизированный
- Краткие сообщения если вопрос простой
- Развёрнутые если нужен анализ или план
- Всегда на русском языке
- Используй имя пользователя

ВАЖНО: Ты помнишь всё из истории разговоров. Обновляй своё понимание профиля человека постоянно."""

ONBOARDING_PROMPT = """Ты начинаешь знакомство с новым пользователем. 
Твоя задача — за несколько сообщений узнать:
1. Как его зовут (если ещё не знаешь)
2. Чем он занимается
3. Какие главные цели на ближайшее время
4. Какой у него ритм дня (жаворонок/сова, когда активен)
5. Какие трекеры хочет вести (эмоции, физика, энергия, другое)
6. Что для него сейчас самое важное

Знакомься тепло и естественно — как живой человек, не анкета.
Задавай по 1-2 вопроса за раз, не засыпай сразу всем списком.
Отвечай на русском языке."""

async def ask_claude(messages, system_prompt):
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    save_user(user_id, name)
    user = get_user(user_id)

    if not user[4]:  # onboarding_done
        greeting = await ask_claude(
            [{"role": "user", "content": f"Привет! Меня зовут {name}"}],
            ONBOARDING_PROMPT
        )
        save_message(user_id, "assistant", greeting)
        await update.message.reply_text(greeting)
    else:
        await update.message.reply_text(f"С возвращением, {name}! Чем могу помочь?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    user_message = update.message.text

    save_user(user_id, name)
    user = get_user(user_id)
    save_message(user_id, "user", user_message)
    history = get_history(user_id)

    # Определяем какой промпт использовать
    if not user[4]:  # онбординг не завершён
        system = ONBOARDING_PROMPT
        # Проверяем завершён ли онбординг
        if len(history) >= 10:
            set_onboarding_done(user_id)
    else:
        system = get_system_prompt(user)

    response = await ask_claude(history, system)
    save_message(user_id, "assistant", response)

    # Обновляем профиль на основе разговора
    await update_profile_from_conversation(user_id, user_message, response)

    await update.message.reply_text(response)

async def update_profile_from_conversation(user_id, user_message, bot_response):
    user = get_user(user_id)
    current_profile = json.loads(user[2]) if user[2] else {}

    update_prompt = f"""На основе этого сообщения пользователя обнови профиль если есть новая важная информация.
Текущий профиль: {json.dumps(current_profile, ensure_ascii=False)}
Сообщение пользователя: {user_message}

Если есть что добавить — верни обновлённый JSON профиля.
Если ничего важного — верни текущий профиль без изменений.
Возвращай ТОЛЬКО JSON без пояснений."""

    try:
        updated = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": update_prompt}]
        )
        new_profile = json.loads(updated.content[0].text)
        update_profile(user_id, new_profile)
    except:
        pass

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tasks = get_active_tasks(user_id)
    if not tasks:
        await update.message.reply_text("Активных задач нет. Расскажи что планируешь — я запишу!")
        return
    text = "📋 Твои активные задачи:\n\n"
    for i, (title, priority, due) in enumerate(tasks, 1):
        due_str = f" — до {due}" if due else ""
        text += f"{i}. {title} [{priority}]{due_str}\n"
    await update.message.reply_text(text)

async def morning_briefing(context):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT user_id, name FROM users WHERE onboarding_done=1")
    users = c.fetchall()
    conn.close()

    for user_id, name in users:
        tasks = get_active_tasks(user_id)
        tasks_text = "\n".join([f"• {t[0]}" for t in tasks[:5]]) if tasks else "задач нет"
        msg = f"☀️ Доброе утро, {name}!\n\nТвои дела на сегодня:\n{tasks_text}\n\nКак ты сегодня? Что планируешь?"
        try:
            await context.bot.send_message(chat_id=user_id, text=msg)
        except:
            pass

async def evening_checkin(context):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT user_id, name FROM users WHERE onboarding_done=1")
    users = c.fetchall()
    conn.close()

    for user_id, name in users:
        msg = f"🌙 Добрый вечер, {name}!\n\nКак прошёл день? Что удалось сделать?\nЕсть что перенести на завтра или добавить новое?"
        try:
            await context.bot.send_message(chat_id=user_id, text=msg)
        except:
            pass

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", tasks_command))

    # Обработчик сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Утренняя и вечерняя рассылка
    job_queue = app.job_queue
    job_queue.run_daily(morning_briefing, time=time(8, 0))
    job_queue.run_daily(evening_checkin, time=time(21, 0))

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
