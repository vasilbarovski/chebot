import os
import random
import sqlite3
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    Application
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            score INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id INTEGER PRIMARY KEY
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            tag TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            option1 TEXT,
            option2 TEXT,
            option3 TEXT,
            option4 TEXT,
            answer TEXT
        )
    """)
    
    # Заполним базу тестовым вопросом, если она пуста
    cursor.execute("SELECT COUNT(*) FROM questions")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO questions (question, option1, option2, option3, option4, answer)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("Какая планета четвертая от Солнца?", "Венера", "Марс", "Юпитер", "Сатурн", "Марс"))
        
    conn.commit()
    conn.close()

def update_score(user_id, name, points):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        cursor.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (points, user_id))
    else:
        cursor.execute("INSERT INTO users (user_id, name, score) VALUES (?, ?, ?)", (user_id, name, points))
    conn.commit()
    conn.close()

def get_top_players(limit=5):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, score FROM users ORDER BY score DESC LIMIT ?", (limit,))
    top = cursor.fetchall()
    conn.close()
    return top

def add_subscriber(user_id):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def get_random_question():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions ORDER BY RANDOM() LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "question": row[1],
            "options": [row[2], row[3], row[4], row[5]],
            "answer": row[6]
        }
    return None

# === ПЛАНИРОВЩИК (APScheduler) ===
async def send_daily_meme(context: ContextTypes.DEFAULT_TYPE):
    print("Запускается ежедневная рассылка...")
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM subscribers")
    users = cursor.fetchall()
    conn.close()

    meme_folder = "memes"
    if not os.path.exists(meme_folder):
        os.makedirs(meme_folder)
        print(f"Создана папка '{meme_folder}'. Добавьте туда картинки.")
        return

    meme_list = os.listdir(meme_folder)
    if not meme_list:
        print("Нет мемов для рассылки.")
        return

    meme_file = random.choice(meme_list)
    meme_path = os.path.join(meme_folder, meme_file)

    for (user_id,) in users:
        try:
            # Использование context.bot предпочтительнее глобальных переменных
            with open(meme_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption="🗓 Вот твой мем дня!"
                )
        except Exception as e:
            print(f"Ошибка при отправке мема пользователю {user_id}: {e}")

# Функция инициализации задач внутри Event Loop
async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    # Передаем context в задачу рассылки через job_defaults или args
    scheduler.add_job(
        send_daily_meme, 
        CronTrigger(hour=10, minute=0), # Каждый день в 10:00
        args=[ContextTypes.DEFAULT_TYPE(application)] 
    )
    scheduler.start()
    print("Планировщик APScheduler успешно запущен!")

# === КОМАНДЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    add_subscriber(user_id)
    await update.message.reply_text("Привет! Я бот: создаю мемы и провожу викторины 🤖\nИспользуй /menu для старта.")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/menu — открыть меню\n/start — перезапуск\n/top — таблица лидеров")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_players = get_top_players()
    if not top_players:
        await update.message.reply_text("Пока никто не набрал очков 😢")
        return
    text = "🏆 Топ игроков:\n\n"
    for i, (name, score) in enumerate(top_players, start=1):
        text += f"{i}. {name} — {score} очков\n"
    await update.message.reply_text(text)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if "привет" in text:
        await update.message.reply_text("Привет! Рад тебя видеть 👋")
    else:
        # Если бот ждал текст для мема, перехватываем его специальным хендлером
        if context.user_data.get("wait_for_text"):
            await handle_meme_text(update, context)
        else:
            await update.message.reply_text(update.message.text)

# === МЕНЮ ===
keyboard = [
    [InlineKeyboardButton("🎲 Случайный Мем", callback_data="random_meme"),
     InlineKeyboardButton("🖼️ Создать Мем", callback_data="create_meme")],
    [InlineKeyboardButton("❓ Викторина", callback_data="quiz"),
     InlineKeyboardButton("🏆 Топ Игроков", callback_data="top")]
]
menu = InlineKeyboardMarkup(keyboard)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выбери действие:", reply_markup=menu)

# === ВИКТОРИНА (Логика отправки и проверки) ===
async def send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = get_random_question()
    if not q:
        msg = "В базе данных нет вопросов для викторины 😢"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    # Сохраняем правильный ответ в память сессии пользователя
    context.user_data["quiz_answer"] = q["answer"]
    
    # Кнопки вариантов ответов (клик вернет 'answer_Индекс')
    buttons = []
    for idx, option in enumerate(q["options"]):
        buttons.append([InlineKeyboardButton(option, callback_data=f"answer_{idx}_{q['id']}")])
        
    quiz_markup = InlineKeyboardMarkup(buttons)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(q["question"], reply_markup=quiz_markup)
    else:
        await update.message.reply_text(q["question"], reply_markup=quiz_markup)

async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    # Извлекаем индекс нажатой кнопки
    parts = data.split("_")
    opt_idx = int(parts[1])
    
    # Получаем текст выбранного ответа по его индексу из разметки сообщения
    chosen_answer = query.message.reply_markup.inline_keyboard[opt_idx][0].text
    correct_answer = context.user_data.get("quiz_answer")

    user = query.from_user
    name = user.first_name if user.first_name else "Игрок"

    if chosen_answer == correct_answer:
        update_score(user.id, name, 10)
        await query.edit_message_text(f"🎉 Правильно! Это {correct_answer}.\nВам начислено 10 очков! 🏆")
    else:
        await query.edit_message_text(f"❌ Неверно! Правильный ответ: {correct_answer}.")

# === ОБРАБОТКА КНОПОК ===
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "random_meme":
        await query.edit_message_text("🎲 Функция в разработке. Скоро здесь будут мемы!")
    elif data == "create_meme":
        await query.edit_message_text("🖼️ Пришли фото для мема (как фото, не файлом):")
        context.user_data["wait_for_photo"] = True
    elif data == "quiz":
        await send_quiz(update, context)
    elif data == "top":
        top_players = get_top_players()
        if not top_players:
            await query.edit_message_text("Пока никто не набрал очков 😢")
            return
        text = "🏆 Топ игроков:\n\n"
        for i, (name, score) in enumerate(top_players, start=1):
            text += f"{i}. {name} — {score} очков\n"
        await query.edit_message_text(text)
    elif data.startswith("answer_"):
        await check_answer(update, context, data)

# === ОБРАБОТКА ФОТО ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_for_photo"):
        return
    photo = update.message.photo[-1]
    file = await photo.get_file()
    os.makedirs("temp", exist_ok=True)
    await file.download_to_drive("temp/meme.jpg")
