# -*- coding: utf-8 -*-
import logging
import json
import os
import sqlite3
import random
import uuid
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from google import genai

# Load environment variables
load_dotenv()

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Gemini Client if Key exists
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- DATABASE SETUP ---
DB_NAME = "quiz_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Table to store Quiz Configurations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id TEXT PRIMARY KEY,
            creator_id INTEGER,
            title TEXT,
            topic TEXT,
            q_count INTEGER,
            language TEXT,
            difficulty TEXT,
            options_count INTEGER,
            time_limit INTEGER,
            shuffle TEXT,
            negative REAL,
            questions_json TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- CONVERSATION STATES ---
(TOPIC, Q_COUNT, TITLE, DESCRIPTION, LANGUAGE, 
 EXPLANATION, DIFFICULTY, OPTIONS_COUNT, TIME_LIMIT, SHUFFLE, NEGATIVE) = range(11)

# AI Question Generator helper
def generate_bulk_questions_ai(topic, count, lang, difficulty, options_cnt):
    if not ai_client:
        return mock_questions(topic, count, options_cnt)
    
    prompt = f"""
    Generate exactly {count} multiple-choice quiz questions about '{topic}' in the '{lang}' language (or script format).
    Difficulty level should be: {difficulty}.
    Each question must have exactly {options_cnt} options.
    Identify the correct option using its 0-based index.
    
    You MUST respond ONLY with a valid JSON Array matching this structure:
    [
        {{
            "question": "Sawal Text Here",
            "options": ["Option 1", "Option 2", ...],
            "correct": 0
        }}
    ]
    Do not wrap the response in markdown formatting or ```json blocks. Return raw JSON text only.
    """
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(clean_text)
    except Exception as e:
        logger.error(f"AI Generation Failed: {e}")
        return mock_questions(topic, count, options_cnt)

def mock_questions(topic, count, options_cnt):
    questions = []
    for i in range(1, count + 1):
        opts = [f"Option {j}" for j in range(1, options_cnt + 1)]
        correct_idx = random.randint(0, options_cnt - 1)
        opts[correct_idx] = f"Correct Option {correct_idx + 1}"
        questions.append({
            "question": f"Sample dynamic question {i} about {topic}?",
            "options": opts,
            "correct": correct_idx
        })
    return questions

# --- BOT ROUTINES & HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 **Welcome to AI Auto-Quiz Generator Bot!**\n\n"
        "⚡ Commands Layout:\n"
        "👉 `/autoquiz` - Naya AI Quiz generate karne ki step-by-step process shuru karein.",
        parse_mode="Markdown"
    )

async def autoquiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 **AI Auto-Quiz Configuration Wizard**\n\n"
        "📝 **Step 1:** Send me the Topic or Subject for the quiz.\n"
        "(Example: Ancient History, Python Coding, Geography...)",
        parse_mode="Markdown"
    )
    return TOPIC

async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['topic'] = update.message.text
    reply_keyboard = [['5', '10', '20']]
    await update.message.reply_text(
        f"✅ Topic Saved: *{context.user_data['topic']}*\n\n"
        "🔢 **Step 2:** How many questions do you want?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return Q_COUNT

async def handle_q_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['q_count'] = int(update.message.text)
    await update.message.reply_text(
        f"✅ Questions Count: *{context.user_data['q_count']}*\n\n"
        "📝 **Step 3:** Send me the Title of your quiz.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['title'] = update.message.text
    await update.message.reply_text(
        "✅ Title Saved!\n\n"
        "📝 **Step 4:** Send a Description for this quiz.\n"
        "(Or type `/skipauto` to leave it blank)"
    )
    return DESCRIPTION

async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data['description'] = "None" if text == "/skipauto" else text
    reply_keyboard = [['English', 'Hindi', 'Hinglish']]
    await update.message.reply_text(
        "🌐 **Step 5 — Language**\nChoose quiz output layout language:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return LANGUAGE

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['language'] = update.message.text
    reply_keyboard = [['With Explanation', 'No Explanation']]
    await update.message.reply_text(
        "🧾 **Step 6 — Explanation**\nDo you want explanations?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return EXPLANATION

async def handle_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['explanation'] = update.message.text
    reply_keyboard = [['Easy', 'Medium', 'Hard']]
    await update.message.reply_text(
        "🎚 **Step 7 — Difficulty**\nChoose calculation difficulty:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return DIFFICULTY

async def handle_difficulty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['difficulty'] = update.message.text
    reply_keyboard = [['2 Options', '4 Options']]
    await update.message.reply_text(
        "🛛 **Step 8 — Option Count**\nHow many choices per card?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return OPTIONS_COUNT

async def handle_options_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['options_count'] = int(update.message.text.split()[0])
    reply_keyboard = [['15 sec', '30 sec', '60 sec']]
    await update.message.reply_text(
        "⏱ **Step 9 — Time Limit**\nSet ticker duration:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return TIME_LIMIT

async def handle_time_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['time_limit'] = int(update.message.text.split()[0])
    reply_keyboard = [['Shuffle All', 'No Shuffle']]
    await update.message.reply_text(
        "🔀 **Step 10 — Shuffle**\nMix questions sequence order?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SHUFFLE

async def handle_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['shuffle'] = update.message.text
    reply_keyboard = [['0', '0.25', '0.33', '0.50']]
    await update.message.reply_text(
        "➖ **Step 11 — Negative Marking**\nDeduct offset per error choice:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return NEGATIVE

# Final Summary aur Quiz Generation Confirmation
async def handle_negative_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['negative'] = float(update.message.text)
    
    # Saare data ko variables me extract karna
    data = context.user_data
    creator_id = update.effective_user.id
    
    # Loading message
    await update.message.reply_text("⏳ **Generating AI Questions... Please wait!**", parse_mode="Markdown")
    
    # AI se questions generate karna
    questions = generate_bulk_questions_ai(
        topic=data.get('topic'),
        count=data.get('q_count'),
        lang=data.get('language'),
        difficulty=data.get('difficulty'),
        options_cnt=data.get('options_count')
    )
    
    # Unique Quiz ID generate karna
    quiz_id = f"quiz_{uuid.uuid4().hex[:12]}"
    
    # Database mein quiz save karna
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quizzes 
        (quiz_id, creator_id, title, topic, q_count, language, difficulty, options_count, time_limit, shuffle, negative, questions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        quiz_id,
        creator_id,
        data.get('title'),
        data.get('topic'),
        data.get('q_count'),
        data.get('language'),
        data.get('difficulty'),
        data.get('options_count'),
        data.get('time_limit'),
        data.get('shuffle'),
        data.get('negative'),
        json.dumps(questions)
    ))
    conn.commit()
    conn.close()
    
    # HTML formatting with quiz summary
    summary = (
        "<b>🎉 AI Quiz Generated Successfully!</b>\n\n"
        f"🏷 <b>Title:</b> {data.get('title')}\n"
        f"📝 <b>Questions:</b> {data.get('q_count')}\n"
        f"🌐 <b>Language:</b> {data.get('language')}\n"
        f"🎚 <b>Difficulty:</b> {data.get('difficulty')}\n"
        f"🎛 <b>Options/Q:</b> {data.get('options_count')}\n"
        f"🧾 <b>Explanation:</b> {data.get('explanation')}\n"
        f"⏱ <b>Time/Q:</b> {data.get('time_limit')} sec\n"
        f"🔀 <b>Shuffle:</b> {data.get('shuffle')}\n"
        f"➖ <b>Negative:</b> {data.get('negative')}\n\n"
        "<b>🎮 Ready to Play!</b>"
    )
    
    # Inline buttons for Private Chat and Group Chat
    keyboard = [
        [InlineKeyboardButton("🎮 Start Quiz in Private", callback_data=f"start_private_{quiz_id}")],
        [InlineKeyboardButton("👥 Start Quiz in Group", callback_data=f"start_group_{quiz_id}")],
        [InlineKeyboardButton("🔗 Share Group Link", callback_data=f"share_link_{quiz_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(summary, parse_mode="HTML", reply_markup=markup)
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Quiz setup processing setup abandoned.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN environment variable config values missing inside project space.")
        return
        
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("autoquiz", autoquiz_start)],
        states={
            TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic)],
            Q_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q_count)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_language)],
            EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_explanation)],
            DIFFICULTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_difficulty)],
            OPTIONS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_options_count)],
            TIME_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time_limit)],
            SHUFFLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_shuffle)],
            NEGATIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_negative_and_finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    print("🚀 Quiz Generator Engine Started successfully.")
    application.run_polling()

if __name__ == '__main__':
    main()
