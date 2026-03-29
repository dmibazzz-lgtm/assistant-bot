import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import anthropic

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

histories = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я твой личный ассистент. Чем могу помочь?")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    if uid not in histories:
        histories[uid] = []
    histories[uid].append({"role": "user", "content": msg})
    r = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        system="Ты личный ассистент. Отвечай на русском языке.",
        messages=histories[uid]
    )
    reply = r.content[0].text
    histories[uid].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
