import os
import sqlite3
import json
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, 
    KeyboardButton, KeyboardButtonPollType, ReplyKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent  
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler, PollAnswerHandler,
    InlineQueryHandler  
)
from telegram.error import NetworkError
from telegram.request import HTTPXRequest

# Enable Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None

# 🔥 FIXED: .env se SUPPORT_GROUP_ID load karne ke liye ye line jodi hai
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID")) if os.getenv("SUPPORT_GROUP_ID") else None

DB_FILE = "quiz_bot.db"

# Global dictionary for active group games memory
GROUP_GAMES = {}

# ====================================================================
# 🔥 FULLY OPERATIONAL GLOBAL CONVERSATION STATES (AUTOMATIC NO-OVERLAP SEQUENCE)
# ====================================================================
# New quiz build flow states (0 to 5)
TITLE, DESCRIPTION, QUESTIONS, PRE_MESSAGE, TIMER, NEGATIVE = range(6)

# Main quiz edit panel menu flows (6 to 9)
EDIT_TITLE, EDIT_DESC, EDIT_TIMER, EDIT_NEGATIVE = range(6, 10)

# Question inner attributes edit panels (10 to 14)
EDIT_QUESTION_TEXT, EDIT_QUESTION_OPTIONS, EDIT_QUESTION_CORRECT, EDIT_QUESTION_EXPLANATION, EDIT_QUESTION_PRE_MESSAGE = range(10, 15)
# ====================================================================

def escape_markdown(text):
    """Escape special characters for Telegram Markdown"""
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_time(seconds):
    """Convert seconds to min:sec format (e.g., 1m 45s)"""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m {secs}s"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (
                quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                title TEXT,
                description TEXT,
                timer INTEGER DEFAULT 30,
                negative_value REAL DEFAULT 0.0 -- 👈 Nayi col successfully configuration synced
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER,
                question_text TEXT,
                options TEXT,
                correct_answer TEXT,
                explanation TEXT,
                pre_message TEXT,
                FOREIGN KEY(quiz_id) REFERENCES quizzes(quiz_id)
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS broadcast_users (chat_id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE IF NOT EXISTS broadcast_groups (chat_id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully with negative_value column")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")
        
def check_active_quiz_creation(user_id, context):
    """Check if user has an active quiz creation in progress"""
    return "quiz_build" in context.user_data and context.user_data["quiz_build"].get("title")
    
async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        # 1. Get the chat and message object
        chat_obj = update.effective_chat
        msg_obj = update.callback_query.message if update.callback_query else update.message
        user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
        
        # 2. Check if the command is used in a group or supergroup
        if chat_obj.type in ['group', 'supergroup']:
            if update.callback_query:
                await update.callback_query.answer("Not allowed here", show_alert=True)
            
            await msg_obj.reply_text(
                "⚠️ यह कमांड केवल प्राइवेट चैट में काम करती है। कृपया मुझे पर्सनल मैसेज (DM) में `/newquiz` भेजें।"
            )
            return ConversationHandler.END  # Stop the conversation handler inside groups

        # 3. Rest of your original code for private chat
        if update.callback_query:
            await update.callback_query.answer()
            
        await msg_obj.reply_text(
            "Let's create a new quiz. First, send me the title of your quiz (e.g., 'Aptitude Test' or '10 questions about bears').\n\n⚠️ Note: Title must be 128 characters or less.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["quiz_build"] = {"title": "", "description": "", "questions": []}
        context.user_data["quiz_build_creator_id"] = user_id
        return TITLE
    except Exception as e:
        logging.error(f"Error in new_quiz_start: {e}")
        # Safeguard if msg_obj is available during an error
        if 'msg_obj' in locals():
            await msg_obj.reply_text("❌ An error occurred. Please try again with /newquiz")
        return ConversationHandler.END

# start handler 
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Broadcast ke liye Chat ID aur Type database me save karein
        chat_id = update.message.chat.id
        chat_type = update.message.chat.type

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 'private' string check
        is_private = str(chat_type) == "private" or (hasattr(chat_type, "value") and chat_type.value == "private")
        
        if is_private:
            cursor.execute("INSERT OR IGNORE INTO broadcast_users (chat_id) VALUES (?)", (chat_id,))
        else:
            cursor.execute("INSERT OR IGNORE INTO broadcast_groups (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        conn.close()

        # 🔥 SMART OLD BUTTONS CLEANUP (PANEL RAHEGA, SIRF BUTTONS GAYAB)
        if not is_private:
            if chat_id in GROUP_GAMES:
                game = GROUP_GAMES[chat_id]
                
                # 1. Purane Welcome Message ke buttons remove karein
                if "welcome_message_id" in game:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=game["welcome_message_id"],
                            reply_markup=None
                        )
                    except Exception:
                        pass
                
                # 2. Agar koi dynamic ready panel active hai toh uske buttons bhee remove karein
                if "setup_message_id" in game:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=game["setup_message_id"],
                            reply_markup=None
                        )
                    except Exception:
                        pass

                # 3. Agar koi purana pause message chal raha hai toh uske buttons bhee remove karein
                if "pause_message_id" in game:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=game["pause_message_id"],
                            reply_markup=None
                        )
                    except Exception:
                        pass

        # ✅ FIXED: context.args deep-linking logic check
        if context.args and len(context.args) > 0:
            first_arg = context.args[0]  
            
            if first_arg.startswith("quiz_"):
                if not is_private and chat_id in GROUP_GAMES:
                    GROUP_GAMES.pop(chat_id, None)

                parts = first_arg.split("_")
                if len(parts) < 2:
                    await update.message.reply_text("❌ Invalid quiz link.")
                    return
                
                try:
                    quiz_id = int(parts[1])
                except ValueError:
                    await update.message.reply_text("❌ Invalid quiz ID format.")
                    return
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("SELECT title, description, timer, negative_value FROM quizzes WHERE quiz_id = ?", (quiz_id,))
                quiz_data = cursor.fetchone()
                
                cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
                total_q_data = cursor.fetchone()
                total_q = total_q_data[0] if total_q_data else 0  
                conn.close()
                
                if not quiz_data:
                    await update.message.reply_text("❌ Quiz data not found.")
                    return

                title, desc, timer, negative_value = quiz_data
                time_disp = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
                db_neg_val = negative_value if negative_value is not None else 0.0
                
                init_text = (
                    f"🎲 *Get ready for the quiz!*\n\n"
                    f"📚 *Title:* {escape_markdown(title)}\n"
                    f"🔥 *Description:* {escape_markdown(desc) if desc else 'No description'}\n"
                    f"🖊️ *Questions:* {total_q}\n"
                    f"⏱ *Time per question:* {time_disp}\n"
                    f"📉 *Negative Marking:* `-{db_neg_val} Marks` per wrong answer\n\n"
                    "🏁 *Click 'I am ready!' to start the quiz.*\n"
                    "🏁 *The quiz will begin when at least 2 people are ready to play. Send /stop to stop it.*"
                )
                
                # 🌟 FIX: Raw dictionary payload use kiya button ko Green colour dene ke liye
                raw_button = {
                    "text": "I am ready!  (0)",
                    "callback_data": f"ready_{quiz_id}",
                    "style": "primary"  # Hara (Green) rang lagane ke liye
                }
                kb = [[raw_button]]
                
                quiz_panel_msg = await update.message.reply_text(
                    init_text, 
                    reply_markup=InlineKeyboardMarkup(kb), 
                    parse_mode="Markdown"
                )
                
                if not is_private:
                    if chat_id not in GROUP_GAMES:
                        GROUP_GAMES[chat_id] = {}
                    GROUP_GAMES[chat_id]["setup_message_id"] = quiz_panel_msg.message_id
                return

        # Welcome message text layout se pehle active quiz check
        if is_private and check_active_quiz_creation(update.message.from_user.id, context):
            await update.message.reply_text(
                "⚠️ **You have an unfinished quiz.** Please finish creating your quiz or send /cancel.\n\n"
                "You cannot start a new quiz or use other commands until you complete this one."
            )
            return

        # Welcome message text layout
        welcome_text = (
            "👋 *Welcome to Premium Quiz Bot!*\n\n"
            "*Aap is bot se quizzes bana kar apne dosto ke sath groups me realtime khel sakte hain.*\n\n"
            "💡 *Check Available Commands:*\n"
            "➤ /help *– Open help center*\n\n"
            "👥 *Add the bot to a group and start quizzes*\n"
            f"📢 *Owner Details:* ID `{OWNER_ID}`"
        )
        
        # 🌟 FIX: Welcome panel ke buttons ko bhi custom color diya (Blue aur Green)
        if is_private:
            kb = [
                [{"text": "🚀 Create New Quiz", "callback_data": "btn_newquiz", "style": "success"}],
                [{"text": "📚 View My Quizzes", "callback_data": "btn_viewquizzes", "style": "primary"}]
            ]
        else:
            bot_username = context.bot.username
            add_url = f"https://t.me/{bot_username}?startgroup=true"
            kb = [
                [{"text": "✨ Add me in your group", "url": add_url, "style": "primary"}]
            ]
        
        welcome_msg = await update.message.reply_text(
            welcome_text, 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode="Markdown"
        )
        
        if not is_private:
            if chat_id not in GROUP_GAMES:
                GROUP_GAMES[chat_id] = {}
            GROUP_GAMES[chat_id]["welcome_message_id"] = welcome_msg.message_id

    except Exception as e:
        logging.error(f"Error in start: {e}", exc_info=True)  
        await update.message.reply_text("❌ An error occurred. Please try again with /start")
                    

# Help command Handel
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.message.chat.id
        chat_type = update.message.chat.type
        is_private = str(chat_type) == "private" or (hasattr(chat_type, "value") and chat_type.value == "private")

        # Check for active quiz creation
        if check_active_quiz_creation(update.message.from_user.id, context):
            await update.message.reply_text(
                "⚠️ **You have an unfinished quiz.** Please finish creating your quiz or send /cancel.\n\n"
                "You cannot use commands until you complete this quiz."
            )
            return
        
        # 🔥 SMART OLD HELP BUTTONS CLEANUP
        if not is_private:
            if chat_id in GROUP_GAMES:
                game = GROUP_GAMES[chat_id]
                if "help_message_id" in game:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=game["help_message_id"],
                            reply_markup=None
                        )
                    except Exception:
                        pass

        help_text = (
            "Help Menu\n\n"
            "Aap is bot se quizzes bana kar apne dosto ke sath groups me realtime khel sakte hain.\n\n"
            "💡 Available Commands:\n"
            "➤ /newquiz – Create a new quiz\n"
            "➤ /quizzes – View your quizzes\n"
            "➤ /start – Start the bot | quiz\n"
            "➤ /stop – Stop running quiz (admin)\n"
            "➤ /cancel – cancel old all activities\n\n"
            "👥 Add the bot to a group and start quizzes\n"
            "📢 For support, contact owner."
        )
        
        # Owner ka dynamic chat link layout (t.me/user?id=...)
        owner_link = f"tg://user?id={OWNER_ID}"
        owner_button = InlineKeyboardButton("📢 Contact Owner", url=owner_link)
        
        # 🔥 CHAT TYPE BASE PAR BUTTONS KA LOGIC
        if is_private:
            # Private chat: Total 3 Buttons (2 purane + 1 contact owner)
            keyboard = [
                [InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")],
                [InlineKeyboardButton("View My Quizzes 📚", callback_data="btn_viewquizzes")],
                [owner_button]
            ]
        else:
            # Group: Total 2 Buttons (Add me + Contact owner)
            bot_username = context.bot.username
            add_url = f"https://t.me/{bot_username}?startgroup=true"
            keyboard = [
                [InlineKeyboardButton("➕ Add me in your group", url=add_url)],
                [owner_button]
            ]
            
        # Help message send karke use variable me liya
        help_msg = await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        # Agar ye group hai, toh help message ki ID save karein taaki agli baar ye delete ho sake
        if not is_private:
            if chat_id not in GROUP_GAMES:
                GROUP_GAMES[chat_id] = {}
            GROUP_GAMES[chat_id]["help_message_id"] = help_msg.message_id
            
    except Exception as e:
        logging.error(f"Error in help_command: {e}")

# ========================================
# 🔴 NEW COMMAND: /quizzes
# ========================================

async def quizzes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user's quizzes directly via /quizzes command"""
    try:
        # 1. Group check: Send warning and block if used in group/supergroup
        if update.effective_chat.type in ['group', 'supergroup']:
            await update.message.reply_text(
                "⚠️ यह कमांड केवल प्राइवेट चैट में काम करती है। कृपया मुझे पर्सनल मैसेज (DM) में `/quizzes` भेजें।"
            )
            return

        # Check for active quiz creation
        if check_active_quiz_creation(update.message.from_user.id, context):
            await update.message.reply_text(
                "⚠️ **You have an unfinished quiz.** Please finish creating your quiz or send /cancel.\n\n"
                "You cannot use commands until you complete this quiz."
            )
            return
        
        user_id = update.message.from_user.id
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Fetch quizzes with question count
        cursor.execute("""
            SELECT q.quiz_id, q.title, q.timer, COUNT(qu.id) as question_count
            FROM quizzes q
            LEFT JOIN questions qu ON q.quiz_id = qu.quiz_id
            WHERE q.creator_id = ?
            GROUP BY q.quiz_id
            ORDER BY q.quiz_id DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            keyboard = [[InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")]]
            await update.message.reply_text(
                text="❌ Aapne abhi tak koi quiz nahi banaya hai!\n\nNaya quiz banane ke liye 'Create New Quiz' button click karein.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Build list with View buttons for each quiz - 2 buttons per row
        text = "📚 Aapke Banaye Huye Quizzes:\n\n"
        
        keyboard = []
        for idx, (qid, title, timer, q_count) in enumerate(rows, 1):
            time_display = f"{timer}s" if timer < 60 else f"{timer // 60}m"
            text += f"{idx}. **{escape_markdown(title)}**\n"
            text += f"   ☞ {q_count} question{'s' if q_count != 1 else ''} | {time_display}/Q\n\n"
            # Add View button for each quiz - 2 per row
            if len(keyboard) == 0 or len(keyboard[-1]) == 2:
                keyboard.append([])
            keyboard[-1].append(InlineKeyboardButton(f"📖 Q{idx}", callback_data=f"viewq_{qid}"))
        
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in quizzes_command: {e}")
        if update.message:
            await update.message.reply_text("❌ Error loading quizzes. Please try again.")
            
async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        title = update.message.text.strip()
        
        # 🔴 NEW: Check if title exceeds 128 characters
        if len(title) > 128:
            await update.message.reply_text(
                "⚠️ This title is too long. Please send a new one, 128 characters max."
            )
            return TITLE
        
        context.user_data["quiz_build"]["title"] = title
        await update.message.reply_text(
            "Good. Now send me a description of your quiz. This is optional, you can /skip this step.",
            reply_markup=ReplyKeyboardRemove()
        )
        return DESCRIPTION
    except Exception as e:
        logging.error(f"Error in receive_title: {e}")
        return TITLE

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        text = update.message.text
        context.user_data["quiz_build"]["description"] = "" if text.lower() == "/skip" else text.strip()
        
        # ========================================
        # 🔴 SHOW BOTTOM CONTAINER (QUESTIONS STATE)
        # ========================================
        poll_button = KeyboardButton(
            text="Create a Question",
            request_poll=KeyboardButtonPollType(type="quiz")
        )
        bottom_container = ReplyKeyboardMarkup(
            [[poll_button]], 
            resize_keyboard=True,
            one_time_keyboard=False
        )
        
        await update.message.reply_text(
            f"Good. Your quiz '{context.user_data['quiz_build']['title']}' now has 0 questions.\n\n"
            "💡 Now send me a poll with your first question.\n\n"
            "Enable Quiz Mode, add 2-7 options, pick the correct one, and tap Create.\n\n"
            "Warning: this bot can't create anonymous poll\n"
             "Users in groups will see votes from other members.",
            reply_markup=bottom_container
        )
        return QUESTIONS
    except Exception as e:
        logging.error(f"Error in receive_desc: {e}")
        return DESCRIPTION

async def receive_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        poll = update.message.poll
        if poll.type != "quiz":
            await update.message.reply_text("❌ Kripya Quiz mode wala poll hi send karein:")
            return QUESTIONS
        if len(poll.options) > 7:
            await update.message.reply_text("❌ Maximum 7 options allowed. Re-send poll:")
            return QUESTIONS

        opts = [o.text for o in poll.options]
        q_data = {
            "text": poll.question, "options": opts, "correct": opts[poll.correct_option_id],
            "explanation": poll.explanation if poll.explanation else "", "pre_message": ""
        }
        context.user_data["quiz_build"]["questions"].append(q_data)
        context.user_data["current_question_index"] = len(context.user_data["quiz_build"]["questions"]) - 1
        
        await update.message.reply_text(
            f"✅ Question added! Your quiz now has {len(context.user_data['quiz_build']['questions'])} question.\n\n"
            "⚡ Quick options:\n"
            "➤ 📎 Send media | details (text, image, video, etc.) that will be add context\n"
            "➤ 📄 Send text message for pre-message\n\n"
            "💬 Optional:\n"
            "➤ ➕ Now Send the next question directly (auto-skips pre-message)\n"
            "➤ ⚠️ Quiz Finish karne ke liye Pre-message set kare!"
            
        )
        return PRE_MESSAGE
    except Exception as e:
        logging.error(f"Error in receive_poll: {e}")
        await update.message.reply_text("❌ Error processing poll. Please try again.")
        return QUESTIONS

async def receive_pre_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        current_idx = context.user_data.get("current_question_index", -1)
        
        if current_idx < 0 or current_idx >= len(context.user_data.get("quiz_build", {}).get("questions", [])):
            await update.message.reply_text("❌ Error: Question not found!")
            return QUESTIONS
        
        # Agar user PRE_MESSAGE state mein /undo likhta hai
        if update.message.text and update.message.text.strip().lower() == "/undo":
            return await handle_undo(update, context)
        
        # Check if a new poll is being sent - auto-skip pre-message
        if update.message.poll:
            # Auto-skip pre-message and process the new poll
            context.user_data["quiz_build"]["questions"][current_idx]["pre_message"] = ""
            
            # Process the new poll
            poll = update.message.poll
            if poll.type != "quiz":
                await update.message.reply_text("❌ Kripya Quiz mode wala poll hi send karein:")
                return PRE_MESSAGE
            if len(poll.options) > 7:
                await update.message.reply_text("❌ Maximum 7 options allowed. Re-send poll:")
                return PRE_MESSAGE

            opts = [o.text for o in poll.options]
            q_data = {
                "text": poll.question, "options": opts, "correct": opts[poll.correct_option_id],
                "explanation": poll.explanation if poll.explanation else "", "pre_message": ""
            }
            context.user_data["quiz_build"]["questions"].append(q_data)
            context.user_data["current_question_index"] = len(context.user_data["quiz_build"]["questions"]) - 1
            
            # 🟢 FIXED: Naya poll aane par purana bottom keyboard hide karne ke liye ReplyKeyboardRemove joda hai
            await update.message.reply_text(
                f"✅ Question added! Your quiz now has {len(context.user_data['quiz_build']['questions'])} question.\n\n"
                "⚡ Quick options:\n"
                "➤ 📎 Send media | details (text, image, video, etc.) that will be add context\n"
                "➤ 📄 Send text message for pre-message\n\n"
                "💬 Optional:\n"
                "➤ ➕ Now Send the next question directly (auto-skips pre-message)\n"
                "➤ ⚠️ Quiz Finish karne ke liye Pre-message set kare!",
                reply_markup=ReplyKeyboardRemove() # 👈 Isse bottom button temporary hide ho jayega jab tak pre-message bhej rahe ho
            )
            return PRE_MESSAGE
        
        # Handle /skip command
        if update.message.text and update.message.text.lower() == "/skip":
            context.user_data["quiz_build"]["questions"][current_idx]["pre_message"] = ""
        else:
            # Store text or media caption
            if update.message.text:
                context.user_data["quiz_build"]["questions"][current_idx]["pre_message"] = update.message.text.strip()
            elif update.message.caption:
                context.user_data["quiz_build"]["questions"][current_idx]["pre_message"] = update.message.caption.strip()
            else:
                context.user_data["quiz_build"]["questions"][current_idx]["pre_message"] = ""
        
        # ========================================
        # 🔴 SHOW BOTTOM CONTAINER (QUESTIONS STATE)
        # ========================================
        poll_button = KeyboardButton(
            text="Create a Question",
            request_poll=KeyboardButtonPollType(type="quiz")
        )
        bottom_container = ReplyKeyboardMarkup(
            [[poll_button]], 
            resize_keyboard=True,
            one_time_keyboard=False
        )
        
        await update.message.reply_text(
            f"✅ Pre-message set! Your quiz now has {len(context.user_data['quiz_build']['questions'])} question(s).\n\n"
            "💬 Next step:\n"
            "➤ Send next question poll\n"
            "✨ Or\n"
            "➤ type /done to finish quiz\n"
            "↩️ Galti se galat pre-message set ho gaya? Type /undo to delete it.",
            reply_markup=bottom_container
        )
        return QUESTIONS
    except Exception as e:
        logging.error(f"Error in receive_pre_message: {e}")
        return QUESTIONS
            
async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        quiz = context.user_data.get("quiz_build")
        if quiz and quiz["questions"]:
            quiz["questions"].pop()
            
            # ========================================
            # 🔴 KEEP BOTTOM CONTAINER (STILL IN QUESTIONS STATE)
            # ========================================
            poll_button = KeyboardButton(
                text="Create a Question",
                request_poll=KeyboardButtonPollType(type="quiz")
            )
            bottom_container = ReplyKeyboardMarkup(
                [[poll_button]], 
                resize_keyboard=True,
                one_time_keyboard=False
            )
            
            await update.message.reply_text(
                f"↩️ Last question removed! Quiz now has {len(quiz['questions'])} question(s).\n\nSend next question or /done.",
                reply_markup=bottom_container
            )
        else:
            await update.message.reply_text("❌ No questions to remove!")
        return QUESTIONS
    except Exception as e:
        logging.error(f"Error in handle_undo: {e}")
        return QUESTIONS

async def finish_quiz_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        quiz = context.user_data.get("quiz_build", {})
        if not quiz or not quiz.get("questions"):
            await update.message.reply_text("❌ Error: Quiz must have at least 1 question!")
            return QUESTIONS
        
        # ====================================================================
        # 🟢 FIXED: /done bhejte hi sabse pehle bottom question container ko hide karein
        # ====================================================================
        await update.message.reply_text(
            "⏳ Saving questions and closing creator panel...", 
            reply_markup=ReplyKeyboardRemove() # 👈 Isse "Create a Question" container permanently screen se hat jayega
        )
        
        # Inline Buttons for timer setup
        timer_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏱️ 15s", callback_data="timer_15"),
                InlineKeyboardButton("⏱️ 30s", callback_data="timer_30")
            ],
            [
                InlineKeyboardButton("⏱️ 40s", callback_data="timer_40"),
                InlineKeyboardButton("⏱️ 60s", callback_data="timer_60")
            ]
        ])
        
        # Ab timer select karne ke liye main options bhejenge
        await update.message.reply_text(
            "⏱️ Please set a time limit for questions:\n\n"
            "Select an option from the buttons below or type any of these: 15, 30, 40, 60\n\n"
            "Example: Type '30' for 30 seconds per question",
            reply_markup=timer_keyboard
        )
        return TIMER
    except Exception as e:
        logging.error(f"Error in finish_quiz_creation: {e}")
        return QUESTIONS
        
async def handle_timer_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            text = query.data.replace("timer_", "").strip()
            msg_target = query.message
        else:
            text = update.message.text.strip()
            msg_target = update.message

        time_map = {"15": 15, "30": 30, "40": 40, "60": 60}
        
        if text not in time_map:
            await msg_target.reply_text("❌ Invalid time. Please enter: 15, 30, 40, or 60")
            return TIMER
        
        # Timer value temporary save karein
        context.user_data["quiz_build"]["timer"] = time_map[text]
        
        if update.callback_query:
            await msg_target.edit_reply_markup(reply_markup=None)

        # 🔥 Custom buttons aapki demand ke mutabik: 0.25, 0.5, 1.0, 1.5
        neg_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❌ No Negative (0.0)", callback_data="neg_0.0"),
                InlineKeyboardButton("📉 1/4th (-0.25)", callback_data="neg_0.25")
            ],
            [
                InlineKeyboardButton("📉 Half (-0.5)", callback_data="neg_0.5"),
                InlineKeyboardButton("📉 Single (-1.0)", callback_data="neg_1.0")
            ],
            [
                InlineKeyboardButton("📉 Heavy (-1.5)", callback_data="neg_1.5")
            ]
        ])
        
        await msg_target.reply_text(
            "🛑 **Select Negative Marking Schema:**\n\n"
            "Aap is quiz ke liye kitni negative marking set karna chahte hain? Niche diye gaye buttons se choose karein:",
            reply_markup=neg_keyboard,
            parse_mode="Markdown"
        )
        return NEGATIVE 
    except Exception as e:
        logging.error(f"Error in handle_timer_text: {e}")
        return TIMER
        
        
async def view_my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches and displays all quizzes created by the user with View buttons - 2 per row"""
    try:
        # Check for active quiz creation
        if check_active_quiz_creation(update.callback_query.from_user.id, context):
            await update.callback_query.answer(
                "⚠️ You have an unfinished quiz. Please finish creating your quiz or send /cancel.",
                show_alert=True
            )
            return
        
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Fetch quizzes with question count
        cursor.execute("""
            SELECT q.quiz_id, q.title, q.timer, COUNT(qu.id) as question_count
            FROM quizzes q
            LEFT JOIN questions qu ON q.quiz_id = qu.quiz_id
            WHERE q.creator_id = ?
            GROUP BY q.quiz_id
            ORDER BY q.quiz_id DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            keyboard = [[InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")]]
            await query.edit_message_text(
                text="❌ Aapne abhi tak koi quiz nahi banaya hai!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Build list with View buttons for each quiz - 2 buttons per row
        text = "📚 *Aapke Banaye Huye Quizzes:*\n\n"
        
        keyboard = []
        for idx, (qid, title, timer, q_count) in enumerate(rows, 1):
            time_display = f"{timer}s" if timer < 60 else f"{timer // 60}m"
            text += f"{idx}. **{escape_markdown(title)}**\n"
            text += f"   ☞ {q_count} question{'s' if q_count != 1 else ''} | {time_display}/Q\n\n"
            # Add View button for each quiz - 2 per row
            if len(keyboard) == 0 or len(keyboard[-1]) == 2:
                keyboard.append([])
            keyboard[-1].append(InlineKeyboardButton(f"📖 Q{idx}", callback_data=f"viewq_{qid}"))
        
        # Back button on its own row
        keyboard.append([InlineKeyboardButton("Back to Main Menu 🔙", callback_data="back_main")])
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in view_my_quizzes: {e}")
        await query.answer("❌ Error loading quizzes", show_alert=True)

async def handle_view_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles opening summary panel from the quiz list"""
    query = update.callback_query
    try:
        # Callback query format validation (Bug #6 Fix)
        if not query.data or "_" not in query.data:
            await query.answer("❌ Invalid callback data format", show_alert=True)
            return
            
        parts = query.data.split("_")
        if len(parts) < 2:
            await query.answer("❌ Invalid callback data format", show_alert=True)
            return

        # Quiz ID parse aur check
        try:
            quiz_id = int(parts[1])
        except (ValueError, IndexError):
            await query.answer("❌ Invalid quiz ID format", show_alert=True)
            return

        # Sab sahi hone par process aage badhayenge
        await query.answer()
        
        try:
            await query.message.delete()
        except Exception as delete_error:
            logging.warning(f"Could not delete message in handle_view_quiz_callback: {delete_error}")

        await show_summary_panel(query, context, quiz_id)

    except Exception as e:
        logging.error(f"Error in handle_view_quiz_callback: {e}")
        try:
            await query.answer("❌ Error loading quiz", show_alert=True)
        except Exception:
            pass
            
async def show_summary_panel(query, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            await query.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, description, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username if context.bot.username else "quiz_bot"
        escaped_title = escape_markdown(title)
        escaped_desc = escape_markdown(description) if description else "No description"
        
        summary_text = (
            "👍 *Here's your quiz:*\n\n"
            f"💌 Title: **{escaped_title}**\n"
            f"🫥 **Description:** {escaped_desc}\n"
            f"⚡ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"`https://t.me/{bot_username}?start=quiz_{quiz_id}`"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("Start quiz in Private Chat", callback_data=f"startprivate_{quiz_id}")],
            [InlineKeyboardButton("Start quiz in Group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("Share Quiz", switch_inline_query=f"quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit", callback_data=f"edit_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await query.message.reply_text(summary_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in show_summary_panel: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)}")

async def show_summary_panel_text(update, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            await update.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, description, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username if context.bot.username else "quiz_bot"
        escaped_title = escape_markdown(title)
        escaped_desc = escape_markdown(description) if description else "No description"
        
        summary_text = (
            "🏁 *Here's your quiz:*\n\n"
            f"📒 **Title: {escaped_title}**\n"
            f"🫥 **Description:** {escaped_desc}\n"
            f"⚡ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"`https://t.me/{bot_username}?start=quiz_{quiz_id}`"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("Start Private Chat", callback_data=f"startprivate_{quiz_id}")],
            [InlineKeyboardButton("Start Quiz in Group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("Share Quiz", switch_inline_query=f"quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit", callback_data=f"edit_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await update.message.reply_text(summary_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in show_summary_panel_text: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def handle_start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle private chat quiz start - requires only 1 user"""
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        
        await query.edit_message_text(
            text="🎮 **Private Mode**\n\nAap akele is quiz ko start karne ke liye ready ho gaye?\n\nClick 'Confirm' to begin!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm Start", callback_data=f"confirm_private_{quiz_id}")]
            ])
        )
    except Exception as e:
        logging.error(f"Error in handle_start_private: {e}")
        await query.answer("❌ Error", show_alert=True)

async def handle_negative_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        neg_val = float(query.data.replace("neg_", "").strip())
        quiz = context.user_data.get("quiz_build", {})
        user_id = context.user_data.get("quiz_build_creator_id")
        
        if not quiz or not quiz.get("title"):
            await query.message.reply_text("❌ Error: Quiz data missing. Start over with /newquiz")
            return ConversationHandler.END

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # negative_value column ke sath data insert kiya
        cursor.execute(
            "INSERT INTO quizzes (creator_id, title, description, timer, negative_value) VALUES (?, ?, ?, ?, ?)", 
            (user_id, quiz["title"], quiz["description"], quiz["timer"], neg_val)
        )
        qid = cursor.lastrowid
        
        for q in quiz["questions"]:
            cursor.execute(
                "INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation, pre_message) VALUES (?, ?, ?, ?, ?, ?)", 
                (qid, q["text"], json.dumps(q["options"]), q["correct"], q["explanation"], q["pre_message"])
            )
        conn.commit()
        conn.close()
        
        context.user_data.pop("quiz_build", None)
        context.user_data.pop("quiz_build_creator_id", None)
        
        await query.message.edit_reply_markup(reply_markup=None)
        
        neg_display = "Disabled" if neg_val == 0.0 else f"-{neg_val} per wrong answer"
        await query.message.reply_text(f"✅ Quiz Created Successfully!\n⏱ Timer: {quiz['timer']}s\n📉 Negative Marking: {neg_display}")
        
        await show_summary_panel_text(query, context, qid)
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in handle_negative_selection: {e}")
        return ConversationHandler.END
    
async def handle_confirm_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and start private quiz with 1 user"""
    try:
        query = update.callback_query
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        quiz_id = int(query.data.split("_")[2])
        
        await query.answer("🚀 Quiz shuru ho rahi hai!")
        await query.edit_message_text("⏳ Quiz loading... Please wait!")
        
        if chat_id not in GROUP_GAMES:
            GROUP_GAMES[chat_id] = {
                "quiz_id": quiz_id,
                "joined_users": {user_id: query.from_user.first_name or "Player"},
                "current_q": 0,
                "scores": {user_id: {"score": 0, "total_time": 0.0}},
                "poll_map": {},
                "start_time": None,
                "user_answers": {user_id: {}},
                "question_start_times": {},
                "ready_users": {user_id},
                "quiz_started": True,
                "poll_message_ids": {},
                "setup_message_id": None,
                "is_private": True,
                "quiz_paused": False,
                "consecutive_no_answers": 0
            }
        
        await asyncio.sleep(1)
        asyncio.create_task(send_next_group_poll(chat_id, context))
    except Exception as e:
        logging.error(f"Error in handle_confirm_private: {e}")
        await query.answer("❌ Error starting quiz", show_alert=True)

async def handle_quiz_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show quiz status/statistics"""
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()
        
        if not quiz_data:
            await query.edit_message_text(text="❌ Quiz not found!")
            return
        
        title, description, timer = quiz_data
        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        
        # 🌟 FIX: Markdown double asterisks (**) ko title string ke dono taraf sahi lagaya hai
        status_text = (
            f"📊 **Quiz Status**\n\n"
            f"**Title:** {escape_markdown(title)}\n"
            f"**Description:** {escape_markdown(description) if description else 'No description'}\n"
            f"**Total Questions:** {total_q[0]}\n"
            f"**Time per Q:** {time_display}\n"
            f"✅ **Status:** Active"
        )
        
        await query.edit_message_text(
            text=status_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"backto_{quiz_id}")]
            ]),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error in handle_quiz_status: {e}")
        await query.answer("❌ Error", show_alert=True)
        
async def edit_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        
        keyboard = [
            [InlineKeyboardButton("Edit Question 📖", callback_data=f"edquestion_{quiz_id}")],
            [InlineKeyboardButton("Edit Title 📝", callback_data=f"edtitle_{quiz_id}")],
            [InlineKeyboardButton("Edit Description ℹ️", callback_data=f"eddesc_{quiz_id}")],
            [InlineKeyboardButton("Edit Timer ⏱", callback_data=f"edtime_{quiz_id}")],
            [InlineKeyboardButton("Edit Negative Marking 📉", callback_data=f"edneg_{quiz_id}")], # 👈 Yeh naya button joda hai
            [InlineKeyboardButton("Back 🔙", callback_data=f"backto_{quiz_id}")]
        ]
        await query.edit_message_text(
            text="⚙️ **Edit Quiz Menu**\n\nAap is quiz ka kya badalna chahte hain? Niche se chunyein:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error in edit_quiz_menu: {e}")
        await query.answer("❌ Error", show_alert=True)
        
async def back_to_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        await query.message.delete()
        await show_summary_panel(query, context, quiz_id)
    except Exception as e:
        logging.error(f"Error in back_to_summary: {e}")

# ==========================================
# ⚙️ FULLY OPERATIONAL QUIZ EDITOR HANDLERS
# ==========================================

async def edit_question_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of questions to edit - 2 buttons per row"""
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, question_text FROM questions WHERE quiz_id = ?", (quiz_id,))
        questions = cursor.fetchall()
        conn.close()
        
        if not questions:
            await query.edit_message_text(
                text="❌ No questions found in this quiz!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"edit_{quiz_id}")]])
            )
            return
        
        text = "📚 Select a question to edit:\n\n"
        keyboard = []
        
        for idx, (q_id, q_text) in enumerate(questions, 1):
            # Truncate long question text for display
            display_text = q_text[:30] + "..." if len(q_text) > 30 else q_text
            text += f"{idx}. {escape_markdown(display_text)}\n"
            # Add button - 2 per row
            if len(keyboard) == 0 or len(keyboard[-1]) == 2:
                keyboard.append([])
            keyboard[-1].append(InlineKeyboardButton(f"Q{idx}", callback_data=f"editq_{quiz_id}_{q_id}"))
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"edit_{quiz_id}")])
        
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error in edit_question_trigger: {e}")
        await query.answer("❌ Error", show_alert=True)

async def show_question_detail_panel(query, context, quiz_id, question_id):
    """Display complete question preview with all action buttons - 1 per row"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, question_text, options, correct_answer, explanation, pre_message FROM questions WHERE id = ? AND quiz_id = ?", (question_id, quiz_id))
        q_data = cursor.fetchone()
        
        # Get question number
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ? AND id < ?", (quiz_id, question_id))
        q_number = cursor.fetchone()[0] + 1
        
        conn.close()
        
        if not q_data:
            await query.answer("❌ Question not found!", show_alert=True)
            return
        
        q_id, q_text, options_json, correct_ans, explanation, pre_message = q_data
        options = json.loads(options_json)
        
        # Build detailed preview message
        detail_text = f"❓ **Question #{q_number}** (Current Status: Active)\n\n"
        detail_text += f"**Question Text:** {escape_markdown(q_text)}\n\n"
        
        detail_text += "👇 Options:\n"
        for idx, opt in enumerate(options, 1):
            status = "✅" if opt == correct_ans else "❌"
            detail_text += f"• {status} {escape_markdown(opt)}"
            if opt == correct_ans:
                detail_text += " (Correct Answer)"
            detail_text += "\n"
        
        detail_text += f"\n⏱️ Timer: 30 seconds\n"
        
        if pre_message:
            detail_text += f"\n💌 Pre-message: {escape_markdown(pre_message)}\n"
        else:
            detail_text += f"\n💌 Pre-message: None set\n"
        
        if explanation:
            detail_text += f"\n📖 Explanation: {escape_markdown(explanation)}\n"
        else:
            detail_text += f"\n📖 Explanation: None set\n"
        
        # Build action buttons - 1 per row (ek ke niche ek)
        keyboard = [
            [InlineKeyboardButton("Pre-message", callback_data=f"editpre_{quiz_id}_{q_id}")],
            [InlineKeyboardButton("Explanation", callback_data=f"editexpl_{quiz_id}_{q_id}")],
            [InlineKeyboardButton("Delete Question", callback_data=f"delq_{quiz_id}_{q_id}")],
            [InlineKeyboardButton("Replace Question", callback_data=f"replaceq_{quiz_id}_{q_id}")],
            [InlineKeyboardButton("Back to Questions List", callback_data=f"edquestion_{quiz_id}")]
        ]
        
        await query.edit_message_text(
            text=detail_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error in show_question_detail_panel: {e}")
        await query.answer("❌ Error loading question details", show_alert=True)

async def handle_question_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle click on specific question to show detail panel"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Parse: editq_quiz_id_question_id
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        question_id = int(parts[2])
        
        await show_question_detail_panel(query, context, quiz_id, question_id)
    except Exception as e:
        logging.error(f"Error in handle_question_detail: {e}")
        await query.answer("❌ Error", show_alert=True)

async def edit_pre_message_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start conversation to edit pre-message"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Parse: editpre_quiz_id_question_id
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        question_id = int(parts[2])
        
        context.user_data["editing_q_id"] = question_id
        context.user_data["editing_quiz_id"] = quiz_id
        
        await query.message.reply_text(
            "💬 **Send the pre-message content** (text, caption, etc.) that will appear before this question.\n\n"
            "Or type /remove to delete the existing pre-message, /skip to cancel."
        )
        return EDIT_QUESTION_PRE_MESSAGE
    except Exception as e:
        logging.error(f"Error in edit_pre_message_trigger: {e}")
        return ConversationHandler.END

async def save_pre_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save edited pre-message"""
    try:
        q_id = context.user_data.get("editing_q_id")
        quiz_id = context.user_data.get("editing_quiz_id")
        text = update.message.text.strip()
        
        if not q_id or not quiz_id:
            await update.message.reply_text("❌ Error: Session expired.")
            return ConversationHandler.END
        
        # Handle /remove or /skip commands
        if text.lower() == "/remove":
            new_pre_msg = ""
        elif text.lower() == "/skip":
            context.user_data.pop("editing_q_id", None)
            context.user_data.pop("editing_quiz_id", None)
            await update.message.reply_text("❌ Cancelled.")
            return ConversationHandler.END
        else:
            new_pre_msg = text
        
        # Update database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE questions SET pre_message = ? WHERE id = ?", (new_pre_msg, q_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_q_id", None)
        context.user_data.pop("editing_quiz_id", None)
        
        await update.message.reply_text("✅ Pre-message updated successfully!")
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_pre_message: {e}")
        await update.message.reply_text("❌ Error saving pre-message.")
        return ConversationHandler.END

async def edit_explanation_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start conversation to edit explanation"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Parse: editexpl_quiz_id_question_id
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        question_id = int(parts[2])
        
        context.user_data["editing_q_id"] = question_id
        context.user_data["editing_quiz_id"] = quiz_id
        
        await query.message.reply_text(
            "📖 **Send the explanation** for the correct answer.\n\n"
            "Or type /remove to delete the existing explanation, /skip to cancel."
        )
        return EDIT_QUESTION_EXPLANATION
    except Exception as e:
        logging.error(f"Error in edit_explanation_trigger: {e}")
        return ConversationHandler.END

async def save_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save edited explanation"""
    try:
        q_id = context.user_data.get("editing_q_id")
        quiz_id = context.user_data.get("editing_quiz_id")
        text = update.message.text.strip()
        
        if not q_id or not quiz_id:
            await update.message.reply_text("❌ Error: Session expired.")
            return ConversationHandler.END
        
        # Handle /remove or /skip commands
        if text.lower() == "/remove":
            new_explanation = ""
        elif text.lower() == "/skip":
            context.user_data.pop("editing_q_id", None)
            context.user_data.pop("editing_quiz_id", None)
            await update.message.reply_text("❌ Cancelled.")
            return ConversationHandler.END
        else:
            new_explanation = text
        
        # Update database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE questions SET explanation = ? WHERE id = ?", (new_explanation, q_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_q_id", None)
        context.user_data.pop("editing_quiz_id", None)
        
        await update.message.reply_text("✅ Explanation updated successfully!")
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_explanation: {e}")
        await update.message.reply_text("❌ Error saving explanation.")
        return ConversationHandler.END

async def handle_delete_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a question with confirmation"""
    try:
        query = update.callback_query
        
        # Parse: delq_quiz_id_question_id
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        question_id = int(parts[2])
        
        # Show confirmation
        await query.edit_message_text(
            text="⚠️ Are you sure you want to delete this question?\n\n"
                 "This action cannot be undone!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirmdel_{quiz_id}_{question_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"editq_{quiz_id}_{question_id}")]
            ])
        )
        await query.answer()
    except Exception as e:
        logging.error(f"Error in handle_delete_question: {e}")
        await query.answer("❌ Error", show_alert=True)

async def confirm_delete_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and execute question deletion"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Parse: confirmdel_quiz_id_question_id
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        question_id = int(parts[2])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        conn.commit()
        conn.close()
        
        await query.edit_message_text(
            text="✅ Question deleted successfully!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Questions", callback_data=f"edquestion_{quiz_id}")]])
        )
    except Exception as e:
        logging.error(f"Error in confirm_delete_question: {e}")
        await query.answer("❌ Error deleting question", show_alert=True)

async def edit_title_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        context.user_data["editing_quiz_id"] = quiz_id
        await query.message.reply_text("📝 Please send the **new title** for your quiz:\n\n⚠️ Note: Title must be 128 characters or less.")
        return EDIT_TITLE
    except Exception as e:
        logging.error(f"Error in edit_title_trigger: {e}")
        return ConversationHandler.END

async def save_edited_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_title = update.message.text.strip()
        quiz_id = context.user_data.get("editing_quiz_id")
        
        # 🔴 NEW: Check if title exceeds 128 characters
        if len(new_title) > 128:
            await update.message.reply_text(
                "⚠️ This title is too long. Please send a new one, 128 characters max."
            )
            return EDIT_TITLE
        
        if not quiz_id:
            await update.message.reply_text("❌ Error: Session expired. Restart using menu.")
            return ConversationHandler.END
            
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE quizzes SET title = ? WHERE quiz_id = ?", (new_title, quiz_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_quiz_id", None)
        await update.message.reply_text("✅ Quiz title successfully updated!")
        await show_summary_panel_text(update, context, quiz_id)
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_edited_title: {e}")
        await update.message.reply_text("❌ Error updating title. Please try again.")
        return ConversationHandler.END

async def edit_desc_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        context.user_data["editing_quiz_id"] = quiz_id
        await query.message.reply_text("ℹ️ Please send the **new description** for your quiz (or type /skip to remove it):")
        return EDIT_DESC
    except Exception as e:
        logging.error(f"Error in edit_desc_trigger: {e}")
        return ConversationHandler.END

async def save_edited_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        text = update.message.text.strip()
        new_desc = "" if text.lower() == "/skip" else text
        quiz_id = context.user_data.get("editing_quiz_id")
        
        if not quiz_id:
            await update.message.reply_text("❌ Error: Session expired.")
            return ConversationHandler.END
            
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE quizzes SET description = ? WHERE quiz_id = ?", (new_desc, quiz_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_quiz_id", None)
        await update.message.reply_text("✅ Quiz description successfully updated!")
        await show_summary_panel_text(update, context, quiz_id)
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_edited_desc: {e}")
        await update.message.reply_text("❌ Error updating description. Please try again.")
        return ConversationHandler.END

async def edit_timer_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        context.user_data["editing_quiz_id"] = quiz_id
        await query.message.reply_text("⏱ Please enter the new per-question timer limit: (15, 30, 40, or 60)")
        return EDIT_TIMER
    except Exception as e:
        logging.error(f"Error in edit_timer_trigger: {e}")
        return ConversationHandler.END

async def save_edited_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        text = update.message.text.strip()
        time_map = {"15": 15, "30": 30, "40": 40, "60": 60}
        
        if text not in time_map:
            await update.message.reply_text("❌ Invalid entry! Please type exactly 15, 30, 40, or 60:")
            return EDIT_TIMER
            
        quiz_id = context.user_data.get("editing_quiz_id")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE quizzes SET timer = ? WHERE quiz_id = ?", (time_map[text], quiz_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_quiz_id", None)
        await update.message.reply_text("✅ Quiz timer configuration updated!")
        await show_summary_panel_text(update, context, quiz_id)
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_edited_timer: {e}")
        await update.message.reply_text("❌ Error updating timer. Please try again.")
        return ConversationHandler.END

async def edit_negative_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show options to change negative marking from edit menu"""
    try:
        query = update.callback_query
        await query.answer()
        quiz_id = int(query.data.split("_")[1])
        context.user_data["editing_quiz_id"] = quiz_id
        
        neg_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❌ No Negative (0.0)", callback_data=f"updeneg_{quiz_id}_0.0"),
                InlineKeyboardButton("📉 1/4th (-0.25)", callback_data=f"updeneg_{quiz_id}_0.25")
            ],
            [
                InlineKeyboardButton("📉 Half (-0.5)", callback_data=f"updeneg_{quiz_id}_0.5"),
                InlineKeyboardButton("📉 Single (-1.0)", callback_data=f"updeneg_{quiz_id}_1.0")
            ],
            [
                InlineKeyboardButton("📉 Heavy (-1.5)", callback_data=f"updeneg_{quiz_id}_1.5")
            ],
            [InlineKeyboardButton("Back 🔙", callback_data=f"edit_{quiz_id}")]
        ])
        
        await query.message.reply_text(
            "⚙️ **Update Negative Marking:**\n\nAap is quiz ke liye kaunsa naya negative marking rule set karna chahte hain?",
            reply_markup=neg_keyboard,
            parse_mode="Markdown"
        )
        return EDIT_NEGATIVE
    except Exception as e:
        logging.error(f"Error in edit_negative_trigger: {e}")
        return ConversationHandler.END

async def save_edited_negative(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the newly selected negative marking value to the database"""
    try:
        query = update.callback_query
        await query.answer()
        
        parts = query.data.split("_")
        quiz_id = int(parts[1])
        new_neg_val = float(parts[2])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE quizzes SET negative_value = ? WHERE quiz_id = ?", (new_neg_val, quiz_id))
        conn.commit()
        conn.close()
        
        context.user_data.pop("editing_quiz_id", None)
        await query.message.edit_reply_markup(reply_markup=None)
        
        neg_display = "Disabled" if new_neg_val == 0.0 else f"-{new_neg_val} per wrong answer"
        await query.message.reply_text(f"✅ Quiz configuration updated successfully!\n📉 New Negative Marking: {neg_display}")
        
        if hasattr(query, 'message') and query.message:
            await show_summary_panel(query, context, quiz_id)
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error in save_edited_negative: {e}")
        return ConversationHandler.END


# ==========================================
# 🎯 SINGLE READY BUTTON DRIVEN ACTIVATION
# ==========================================

async def handle_ready_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-joins users and sets dynamic counter to verify activation benchmarks"""
    try:
        query = update.callback_query
        chat_id = query.message.chat_id
        message_id = query.message.message_id
        user_id = query.from_user.id
        user_name = query.from_user.username if query.from_user.username else query.from_user.first_name
        
        # FIX 1 & 4: Safe string check aur parsing (Type Mismatch se bachne ke liye)
        parts = query.data.split("_")
        if len(parts) < 2:
            try:
                await query.answer("❌ Invalid data format.", show_alert=True)
            except Exception:
                pass
            return
            
        try:
            quiz_id = int(parts[1])
        except ValueError:
            try:
                await query.answer("❌ Invalid Quiz ID format.", show_alert=True)
            except Exception:
                pass
            return
        
        # 🌟 FIX (Bug #6): Quiz IDs ko string ke bajaye integers me comparison kiya taaki purana data properly clean ho
        if chat_id in GROUP_GAMES:
            old_game = GROUP_GAMES[chat_id]
            try:
                old_quiz_id = int(old_game.get("quiz_id", 0))
            except ValueError:
                old_quiz_id = 0

            if old_game.get("quiz_paused") or old_quiz_id != quiz_id:
                if not old_game.get("quiz_started") or old_game.get("setup_message_id") != message_id:
                    del GROUP_GAMES[chat_id]

        if chat_id not in GROUP_GAMES:
            GROUP_GAMES[chat_id] = {
                "quiz_id": quiz_id, 
                "joined_users": {}, 
                "current_q": 0, 
                "scores": {}, 
                "poll_map": {}, 
                "start_time": None,
                "user_answers": {},  
                "question_start_times": {},
                "ready_users": set(),  
                "quiz_started": False,
                "poll_message_ids": {},
                "setup_message_id": message_id,
                "setup_panel_text": query.message.text,
                "is_private": False,
                "quiz_paused": False,
                "consecutive_no_answers": 0
            }
        else:
            # Update setup message ID if not already set
            if GROUP_GAMES[chat_id].get("setup_message_id") is None:
                GROUP_GAMES[chat_id]["setup_message_id"] = message_id
                GROUP_GAMES[chat_id]["setup_panel_text"] = query.message.text
            
        game = GROUP_GAMES[chat_id]

        # 🚀 ANTI-ERROR MULTI-USER BYPASS:
        # Agar countdown chal raha hai, toh users ko error dene ke bajaye silently group me add karein
        if game.get("quiz_started"):
            if "joined_users" not in game: game["joined_users"] = {}
            if "scores" not in game: game["scores"] = {}
            if "user_answers" not in game: game["user_answers"] = {}
            if "ready_users" not in game: game["ready_users"] = set()

            if user_id not in game["joined_users"]:
                game["joined_users"][user_id] = f"@{user_name}" if query.from_user.username else user_name
                game["scores"][user_id] = {"score": 0, "total_time": 0.0, "wrong": 0, "points": 0.0}
                game["user_answers"][user_id] = {}
            game["ready_users"].add(user_id)
            try:
                await query.answer("Aapko chalte countdown me shaamil kar liya gaya hai! ⚡", show_alert=False)
            except Exception:
                pass
            return

        # Auto-Join structure initialization execution
        if user_id not in game["joined_users"]:
            game["joined_users"][user_id] = f"@{user_name}" if query.from_user.username else user_name
            # FIX 2: Scoring dict me default keys complete rakhi hain
            game["scores"][user_id] = {"score": 0, "total_time": 0.0, "wrong": 0, "points": 0.0}
            game["user_answers"][user_id] = {}

        game["ready_users"].add(user_id)
        ready_count = len(game["ready_users"])

        # Check if this is from external sharing link (single player mode)
        is_private_chat = str(query.message.chat.type) == "private" or (hasattr(query.message.chat.type, "value") and query.message.chat.type.value == "private")
        min_ready_required = 1 if is_private_chat else 2

        # 🌟 FIX (Bug #5 Race Condition): State trigger ke badalte hi execution duplicate prevent check lagaya
        if ready_count >= min_ready_required and not game.get("quiz_started"):
            game["quiz_started"] = True
            
            await query.answer("🎯 Target achieved! Quiz start ho rahi hai...")
            
            # Only edit button, keep panel message same
            keyboard = []  # No button - just empty
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            
            # Send countdown messages instead of editing the setup message
            for count in ["🎲 The quiz is about to begin…", "3️⃣....", "2️⃣Ready...", "1️⃣ SET…", "Go..🚀"]:
                countdown_msg = await context.bot.send_message(chat_id=chat_id, text=count)
                await asyncio.sleep(1)
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=countdown_msg.message_id)
                except Exception as e:
                    logging.warning(f"Could not delete countdown message: {e}")

            # Send banner message
            banner_msg = await context.bot.send_message(chat_id=chat_id, text="🔥 Get ready! Quiz shuru ho rahi hai... 🚀")
            await asyncio.sleep(5)
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=banner_msg.message_id)
            except Exception as e:
                logging.warning(f"Could not delete banner message: {e}")
            
            game["current_q"] = 0
            
            # 🌟 DOUBLE CHECK STATE AFTER SLEEP LOOP: Kisi aur concurrent query ne start toh nahi kiya?
            if game.get("current_q") == 0:
                asyncio.create_task(send_next_group_poll(chat_id, context))
        else:
            # Agar quiz already start ho chuki hai countdown me toh unhe welcome alert dein
            if game.get("quiz_started"):
                await query.answer("Quiz start ho rahi hai, aap shamil ho chuke hain! ⚡")
                return

            # 🌟 FIX: Library wrapper ko bypass karne ke liye raw dict format use kiya jo green colour update ko block karega
            raw_live_ready_btn = {
                "text": f"I am ready!  ({ready_count})",
                "callback_data": f"ready_{quiz_id}",
                "style": "success"  # Locks Green color on dynamic dynamic state mutations
            }
            keyboard = [[raw_live_ready_btn]]
            
            # EDIT ONLY THE BUTTON, NOT THE WHOLE MESSAGE
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            await query.answer("Aapne confirmation register kar di! 👍")
            
    except Exception as e:
        logging.error(f"Error in handle_ready_click: {e}")
        try:
            await query.answer("Aap successfully jud chuke hain! 👍", show_alert=False)
        except Exception:
            pass
            
                
async def handle_pause_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quiz pause resume"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Parse: pausequiz_chat_id
        parts = query.data.split("_")
        chat_id = int(parts[1]) # Error fixed here
        
        if chat_id not in GROUP_GAMES:
            await query.answer("❌ Quiz not found", show_alert=True)
            return
        
        game = GROUP_GAMES[chat_id]
        game["quiz_paused"] = False
        game["consecutive_no_answers"] = 0
        
        # Safety line: ID tracking clear karein
        game.pop("pause_message_id", None)
        
        await query.edit_message_text(
            text="Quiz Resuming...\n\n🚀 Next question coming up!",
            reply_markup=InlineKeyboardMarkup([])
        )
        
        await asyncio.sleep(2)
        asyncio.create_task(send_next_group_poll(chat_id, context))
    except Exception as e:
        logging.error(f"Error in handle_pause_quiz: {e}")
        await query.answer("❌ Error", show_alert=True)
        
async def handle_stop_quiz_from_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quiz stop from pause menu"""
    query = update.callback_query
    try:
        await query.answer()
        
        # Parse: stopquiz_chat_id
        parts = query.data.split("_")
        chat_id = int(parts[1])
        
        if chat_id not in GROUP_GAMES:
            await query.answer("❌ Quiz not found", show_alert=True)
            return
            
        game = GROUP_GAMES[chat_id]
        
        # ⚡ फिक्स 1: बैकग्राउंड टाइमर/टास्क को तुरंत मारें (Cancel करें) ताकि अगला सवाल न आए
        if "current_task" in game and not game["current_task"].done():
            game["current_task"].cancel()
            logging.info(f"Quiz background task cancelled from pause menu for chat {chat_id}")
            
        # ⚡ फिक्स 2: अगर ग्रुप में कोई पोल खुला रह गया है, तो उसे तुरंत क्लोज करें
        current_q_idx = game.get("current_q", 0)
        poll_ids_dict = game.get("poll_message_ids", {})
        if current_q_idx in poll_ids_dict:
            try:
                await context.bot.stop_poll(chat_id=chat_id, message_id=poll_ids_dict[current_q_idx])
            except Exception:
                pass # अगर पोल पहले से बंद हो तो एरर न आए
        
        # Tracking clear karein
        game.pop("pause_message_id", None)
        
        await query.edit_message_text(
            text="❌ Quiz stopped!\n\n🏁 Final Result:",
            reply_markup=InlineKeyboardMarkup([])
        )
        
        await compile_group_leaderboard(chat_id, context)
        
        # ⚡ फिक्स 3: रिजल्ट दिखाने के बाद डेटा को मेमोरी से पूरी तरह डिलीट करें
        GROUP_GAMES.pop(chat_id, None)
        
    except Exception as e:
        logging.error(f"Error in handle_stop_quiz_from_pause: {e}", exc_info=True)
        await query.answer("❌ Error", show_alert=True)
        
async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the running quiz in group"""
    try:
        chat_id = update.effective_chat.id
        
        # Check if quiz is running in this chat
        if chat_id not in GROUP_GAMES:
            await update.message.reply_text("❌ Koi quiz is group me chal nahi rahi hai!")
            return
        
        game = GROUP_GAMES[chat_id]
        
        # Check if quiz has started
        if not game.get("quiz_started"):
            await update.message.reply_text("❌ Quiz abhi start hi nahi huya hai!")
            return
            
        # ⚡ फिक्स 1: बैकग्राउंड टाइमर/टास्क को तुरंत मारें (Cancel करें) ताकि अगला सवाल लोड न हो
        if "current_task" in game and not game["current_task"].done():
            game["current_task"].cancel()
            logging.info(f"Quiz background task cancelled via /stop for chat {chat_id}")
            
        # ⚡ फिक्स 2: ग्रुप में खुले हुए चालू पोल (Active Poll) को तुरंत बंद करें
        current_q_idx = game.get("current_q", 0)
        poll_ids_dict = game.get("poll_message_ids", {})
        if current_q_idx in poll_ids_dict:
            try:
                await context.bot.stop_poll(chat_id=chat_id, message_id=poll_ids_dict[current_q_idx])
            except Exception:
                pass # अगर पोल पहले से बंद हो तो क्रैश न हो
        
        # Stop the quiz and show leaderboard
        await update.message.reply_text("Quiz stop ho gaya! Final Result dikha raha hoon...")
        await compile_group_leaderboard(chat_id, context)
        
        # ⚡ फिक्स 3: लीडरबोर्ड दिखाने के बाद तुरंत डेटा हटा दें ताकि मेमोरी पूरी साफ हो जाए
        GROUP_GAMES.pop(chat_id, None)
        
    except Exception as e:
        logging.error(f"Error in stop_quiz: {e}", exc_info=True)
        await update.message.reply_text("❌ Error stopping quiz")
        

async def send_next_group_poll(chat_id, context):
    try:
        game = GROUP_GAMES.get(chat_id)
        if not game:
            return
        
        # Check if quiz is paused
        if game.get("quiz_paused"):
            return
            
        qid = game["quiz_id"]
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # FIX 3: fetchone() null ho sakta hai agar quiz delete ho gayi ho ya ID galat ho
        cursor.execute("SELECT title FROM quizzes WHERE quiz_id = ?", (qid,))
        quiz_title_data = cursor.fetchone()
        if not quiz_title_data:
            logging.error(f"Quiz title not found for quiz_id: {qid}")
            conn.close()
            return
        quiz_title = quiz_title_data[0]
        
        cursor.execute("SELECT timer FROM quizzes WHERE quiz_id = ?", (qid,))
        timer_data = cursor.fetchone()
        
        cursor.execute("SELECT question_text, options, correct_answer, pre_message, explanation FROM questions WHERE quiz_id = ?", (qid,))
        questions = cursor.fetchall()
        conn.close()
        
        if not timer_data or not questions:
            logging.error(f"Quiz data not found for quiz_id: {qid}")
            return
        
        # ⚡ फिक्स 1: सारे सवाल खत्म होने पर डेटा तुरंत क्लियर करें ताकि लीडरबोर्ड के बाद नया टास्क न बने
        if game["current_q"] >= len(questions):
            await compile_group_leaderboard(chat_id, context)
            GROUP_GAMES.pop(chat_id, None)
            return

        # [0] to extract correct integer from database tuple
        raw_timer = timer_data[0] if (timer_data and isinstance(timer_data, tuple)) else 30
        
        # 🕒 SAFETY CHECK: Telegram minimum 10 seconds demand karta hai
        timer = raw_timer if raw_timer >= 10 else 10
        
        q = questions[game["current_q"]]
        q_text, options_json, correct_ans, pre_msg, explanation = q
        options = json.loads(options_json)
        correct_idx = options.index(correct_ans)
        
        # 📢 Context (pre_msg) भेजने के दौरान नेटवर्क एरर सुरक्षा
        if pre_msg:
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"📢 Context: {pre_msg}")
                await asyncio.sleep(1)
            except NetworkError as ne:
                logging.warning(f"Context message failed due to network: {ne}. Continuing to poll.")

        # 🌟 STATE CHECKS BEFORE SENDING
        if chat_id not in GROUP_GAMES or GROUP_GAMES[chat_id].get("quiz_paused"):
            return

        game["question_start_times"][game["current_q"]] = datetime.now()
        game["start_time"] = datetime.now()
        
        # FIX 8: explanation handling
        clean_explanation = explanation.strip() if explanation and str(explanation).strip() else None
        
        # ====================================================================
        # 🔥 FIX: RETRY LOOP FOR POLL SENDING (TIMED OUT ERROR PREVENTION)
        # ====================================================================
        poll_msg = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 🚀 FINAL FIXED POLL WITH RETRY PROTECTION
                poll_msg = await context.bot.send_poll(
                    chat_id=chat_id, 
                    question=f"[{game['current_q'] + 1}/{len(questions)}] {q_text}",
                    options=options, 
                    type="quiz", 
                    correct_option_id=correct_idx,
                    explanation=clean_explanation, 
                    is_anonymous=False,
                    open_period=timer
                )
                break  # अगर पोल सफलतापूर्वक चला गया, तो लूप से बाहर निकलें
            except NetworkError as ne:
                logging.error(f"Attempt {attempt + 1} failed sending poll to {chat_id}: {ne}")
                if attempt < max_retries - 1:
                    logging.info("Network error encountered. Retrying poll in 4 seconds...")
                    await asyncio.sleep(4)  # दोबारा कोशिश करने से पहले थोड़ा रुकें
                else:
                    logging.error("All retries failed for sending poll. Pausing quiz to protect runtime.")
                    game["quiz_paused"] = True
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ *Network problem encountered!* The quiz has been automatically paused. Use /resume to try again.",
                        parse_mode="Markdown"
                    )
                    return
        
        # Safety fallback if poll was somehow not created
        if not poll_msg:
            return
        # ====================================================================
        
        # Store poll message ID for later closing
        game["poll_message_ids"][game["current_q"]] = poll_msg.message_id
        
        game["poll_map"][poll_msg.poll.id] = {
            "correct_idx": correct_idx, 
            "chat_id": chat_id,
            "correct_answer": correct_ans,
            "question_index": game["current_q"]
        }
        
        # ⚡ फिक्स 2: सीधे sleep करने के बजाय इस रनिंग टास्क को ट्रैक करें ताकि बीच में Cancel किया जा सके
        try:
            game["current_task"] = asyncio.current_task()
            await asyncio.sleep(timer)
        except asyncio.CancelledError:
            # अगर /stop कमांड दबाया जाएगा, तो कोड यहीं रुक जाएगा और अगला सवाल कभी नहीं भेजेगा
            logging.info(f"Quiz sleep task cancelled for chat {chat_id}")
            return
        
        # Check if quiz is still active after sleep
        if chat_id not in GROUP_GAMES:
            return
            
        game = GROUP_GAMES[chat_id]
        
        if game.get("quiz_paused"):
            return

        # Check if any user answered this question
        answers_received = False
        if "user_answers" in game:
            for uid, user_answers in game["user_answers"].items():
                if game["current_q"] in user_answers:
                    answers_received = True
                    break
        
        try:
            if not poll_msg.poll.is_closed:
                await context.bot.stop_poll(chat_id=chat_id, message_id=game["poll_message_ids"][game["current_q"]])
        except Exception:
            pass  
        
        if not answers_received:
            game["consecutive_no_answers"] += 1
            logging.info(f"No answers for Q{game['current_q'] + 1}. Count: {game['consecutive_no_answers']}")
            
            # 🔴 AUTO-PAUSE after 2 consecutive questions with no answers
            if game["consecutive_no_answers"] >= 2:
                game["quiz_paused"] = True
                
                pause_msg = f"🔐 The quiz '*{escape_markdown(quiz_title)}*' was paused because nobody was answering"
                
                keyboard = [
                    [InlineKeyboardButton("Resume Quiz", callback_data=f"pausequiz_{chat_id}")],
                    [InlineKeyboardButton("Stop Quiz", callback_data=f"stopquiz_{chat_id}")]
                ]
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=pause_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return
        else:
            game["consecutive_no_answers"] = 0
        
        game["current_q"] += 1
        
        # ⚡ फिक्स 3: अगला टास्क शुरू करने से पहले दोबारा पक्का करें कि गेम अभी भी एक्टिव है
        if chat_id in GROUP_GAMES and not game.get("quiz_paused"):
            asyncio.create_task(send_next_group_poll(chat_id, context))
            
    except Exception as e:
        logging.error(f"Error in send_next_group_poll: {e}", exc_info=True)
                    
        
async def track_poll_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ans = update.poll_answer
        pid = ans.poll_id
        uid = ans.user.id
        user_name = ans.user.first_name or "Player"
        
        for cid, game in list(GROUP_GAMES.items()):
            if "poll_map" in game and pid in game["poll_map"]:
                poll_info = game["poll_map"][pid]
                correct_idx = poll_info["correct_idx"]
                question_idx = poll_info["question_index"]
                
                # FIX 2: Sabse pehle ensure karein ki 'scores' aur 'user_answers' keys game me exist karti hain
                if "scores" not in game:
                    game["scores"] = {}
                if "user_answers" not in game:
                    game["user_answers"] = {}
                if "joined_users" not in game:
                    game["joined_users"] = {}
                
                if uid not in game["joined_users"]:
                    game["joined_users"][uid] = user_name
                    logging.info(f"New participant added: {user_name} (ID: {uid})")
                
                # Agar user pehle se joined nahi tha ya uski entry scores me miss ho gayi thi
                if uid not in game["scores"]:
                    game["scores"][uid] = {"score": 0, "total_time": 0.0, "wrong": 0, "points": 0.0}
                
                if uid not in game["user_answers"]:
                    game["user_answers"][uid] = {}
                
                # FIX 5: ans.option_ids ek list hoti hai, isliye pehla element nikalenge
                # 🌟 OTHERS FIX: Agar user answer retract/unvote karta hai, toh option_ids empty ([]) ho jati hai, use safely -1 handle kiya
                selected_idx = ans.option_ids[0] if ans.option_ids else -1
                
                game["user_answers"][uid][question_idx] = {
                    "selected": selected_idx,  
                    "correct_idx": correct_idx,
                    "timestamp": datetime.now()
                }
                
                # 🌟 ANTI-RACE SYSTEM DEFLATOR: User response active benchmarks ko increment karta hai
                game["consecutive_no_answers"] = 0
                
    except Exception as e:
        logging.error(f"Error in track_poll_answers: {e}")

async def compile_group_leaderboard(chat_id, context):
    try:
        game = GROUP_GAMES.get(chat_id)
        if not game:
            return
        
        bot_username = context.bot.username if context.bot.username else "quiz_bot"
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Title ke sath negative_value column fetch ki
        cursor.execute("SELECT title, negative_value FROM quizzes WHERE quiz_id = ?", (game["quiz_id"],))
        quiz_data = cursor.fetchone()
        quiz_title = quiz_data[0] if quiz_data else "Quiz"
        db_neg_multiplier = quiz_data[1] if (quiz_data and len(quiz_data) > 1) else 0.0
        
        cursor.execute("SELECT question_text, options, correct_answer FROM questions WHERE quiz_id = ?", (game["quiz_id"],))
        questions = cursor.fetchall()
        conn.close()
        
        correct_answers = {}
        for idx, (q_text, options_json, correct_ans) in enumerate(questions):
            options = json.loads(options_json)
            try:
                correct_answers[idx] = options.index(correct_ans)
            except ValueError:
                correct_answers[idx] = -1
        
        final_scores = {}
        for uid in game["user_answers"].keys():
            final_scores[uid] = {"score": 0, "wrong": 0, "total_time": 0.0, "points": 0.0}

        for uid, user_answers in game["user_answers"].items():
            score = 0
            wrong = 0
            total_time = 0.0
            
            for question_idx, answer_data in user_answers.items():
                selected_idx = answer_data["selected"]  
                correct_idx = correct_answers.get(question_idx, -1)
                
                if selected_idx == correct_idx:
                    score += 1
                    start_time = game["question_start_times"].get(question_idx, answer_data["timestamp"])
                    if isinstance(start_time, datetime):
                        elapsed = (answer_data["timestamp"] - start_time).total_seconds()
                        total_time += max(0, elapsed)
                else:
                    wrong += 1
            
            # Core Formula: Right - (Wrong * Selected Button Value)
            calculated_points = float(score) - (float(wrong) * float(db_neg_multiplier))
            final_scores[uid] = {"score": score, "wrong": wrong, "total_time": total_time, "points": calculated_points}
        
        # Dynamic Sorting: Pehle high score (Descending), fir kam time (Ascending)
        sorted_scores = sorted(final_scores.items(), key=lambda item: (-item[1]["points"], item[1]["total_time"]))[:50]
        
        header = f"🏁 The quiz '*{escape_markdown(quiz_title)}*' has finished!\n"
        header += f"📉 *Negative Marking Applied: -{db_neg_multiplier} per wrong answer*\n\n"
        
        total_questions_answered = len(questions)
        subheader = f"📋 {total_questions_answered} questions answered\n"
        subheader += f"👥 Total Participants: {len(final_scores)}\n"
        subheader += f"━━━━━━━━━━━━━━━━━\n\n"
        
        leaderboard = ""
        for idx, (uid, meta) in enumerate(sorted_scores, 1):
            user_display_name = game["joined_users"].get(uid, "Unknown User")
            
            # 🌟 FIX: Agar name @ se shuru hota hai (username hai), toh escape nahi karenge taaki link valid rahe
            if str(user_display_name).startswith("@"):
                clean_username = user_display_name  # Keep pure clickable username
            else:
                clean_username = escape_markdown(user_display_name) # Safe escape for normal names
                
            score = meta["score"]
            wrong_count = meta["wrong"]
            points = meta["points"]
            total_time = format_time(meta["total_time"])
            
            rank_icon = "🥇." if idx == 1 else "🥈." if idx == 2 else "🥉." if idx == 3 else f"{idx}."
            
            # Clean layout print without invalid characters or slashes
            leaderboard += f"{rank_icon} *{clean_username}*\n"
            leaderboard += f"   Final Score: `{points:.2f} Marks`\n"
            leaderboard += f"   Right: `{score}`\n"
            leaderboard += f"   Wrong: `{wrong_count}`\n"
            leaderboard += f"   Total Time Taken: `{total_time}`\n"
            leaderboard += f"   🔹 ┈┈┈┈┈┈┈•┈┈┈┈┈┈┈ 🔹\n"
        
        footer = "\n🏆 Congratulations to all participants!"
        full_message = header + subheader + leaderboard + footer
        
        # 🌟 FIX: Library wrapper ko bypass karke raw dictionary payload bheja taaki crash na ho
        share_url = f"https://t.me/{bot_username}?startgroup=quiz_{game['quiz_id']}"
        
        # Raw structure format dictionary injection
        raw_button = {
            "text": "Start Again ✨",
            "url": share_url,
            "style": "success"  # Hara (Green) rang lagane ke liye. Neela chahiye toh "primary" likhein
        }
        
        # InlineKeyboardMarkup constructor manually object structures feed kar lega
        kb = [[raw_button]]
        
        await context.bot.send_message(
            chat_id=chat_id, 
            text=full_message, 
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )
        GROUP_GAMES.pop(chat_id, None)
    except Exception as e:
        logging.error(f"Error in compile_group_leaderboard: {e}")
        
        
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
        
        # 1. 🔄 COMPLETE FLUSH: Quiz creation ka saara temporary data complete clear karein
        keys_to_clear = [
            "quiz_build", "quiz_build_creator_id", "current_state", 
            "quiz_title", "quiz_desc", "quiz_timer", "questions_list",
            "awaiting_quiz_title", "awaiting_quiz_desc", "awaiting_quiz_timer",
            "awaiting_question_text", "awaiting_options", "awaiting_correct_answer"
        ]
        for key in keys_to_clear:
            if key in context.user_data:
                del context.user_data[key]
                
        # 🌟 FIX: context.bot_data se key ko safely pull kiya aur user_id check karke clean kiya
        if context.bot_data and "active_creations" in context.bot_data:
            if isinstance(context.bot_data["active_creations"], dict) and user_id in context.bot_data["active_creations"]:
                context.bot_data["active_creations"].pop(user_id, None)
            
        # Pure context.user_data dictionary ko verify karein agar state flag bacha ho
        context.user_data.pop("quiz_creation_active", None)

        # 2. Setup Cancel ka message bhej kar purana reply keyboard hatayein
        msg_obj = update.callback_query.message if update.callback_query else update.message
        
        # Safe message call handle
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Setup cancelled.")
            except Exception:
                pass

        await msg_obj.reply_text(
            "❌ *Quiz creation has been cancelled and all temporary data has been cleared.*", 
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="markdown"
        )
        
        # 3. 🔥 DIRECT START PANEL RETURN LOGIC
        # Kuch updates directly main menu display karna prefer karti hain, hum safely start panel return karenge
        await start(update, context)
            
        # 4. Conversation workflow end karein
        return ConversationHandler.END
        
    except Exception as e:
        logging.error(f"Error in cancel: {e}")
        return ConversationHandler.END
        
async def handle_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns to the original main greeting menu with native colored buttons"""
    try:
        query = update.callback_query
        
        # ✅ FIXED: edit_message_text se pehle answer lagaya hai timeout se bachne ke liye
        await query.answer()
        
        welcome_text = (
            "👋 *Welcome to Premium Quiz Bot!*\n\n"
            "*Aap is bot se quizzes bana kar apne dosto ke sath groups me realtime khel sakte hain.*\n\n"
            "💡 *Check Available Commands:*\n"
            "➤ /help *– Open help center*\n\n"
            "👥 *Add the bot to a group and start quizzes*\n"
            f"📢 *Owner Details:* ID `{OWNER_ID}`"
        )
        
        # 🌟 FIX: Raw dictionary payload use kiya buttons ko custom color dene ke liye (Bypassing validation)
        kb = [
            [{"text": "🚀 Create New Quiz", "callback_data": "btn_newquiz", "style": "success"}],     # Hara (Green) color
            [{"text": "📚 View My Quizzes", "callback_data": "btn_viewquizzes", "style": "primary"}]  # Neela (Blue) color
        ]
        
        # ✅ FIXED: parse_mode ko "Markdown" kiya aur timeouts ko api_kwargs me daala
        await query.edit_message_text(
            text=welcome_text, 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode="Markdown",
            api_kwargs={
                "read_timeout": 20,
                "write_timeout": 20
            }
        )
        
    except Exception as e:
        logging.error(f"Error in handle_back_main: {e}", exc_info=True)
        try:
            await query.answer("❌ Error returning to main menu", show_alert=True)
        except Exception:
            pass
            

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline queries to show clean quiz list or share a specific quiz"""
    query = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id
    bot_username = context.bot.username if context.bot.username else "quiz_bot"
    results = []

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # CASE 1: Chat me sirf username type karne par short list dikhao (Empty query)
        if not query:
            cursor.execute(
                "SELECT quiz_id, title, description, timer FROM quizzes WHERE creator_id = ? ORDER BY quiz_id DESC LIMIT 50", 
                (user_id,)
            )
            user_quizzes = cursor.fetchall()
            
            if not user_quizzes:
                results.append(
                    InlineQueryResultArticle(
                        id="no_quiz",
                        title="❌ No Quizzes Found!",
                        description="Aapne koi quiz nahi banaya hai.",
                        input_message_content=InputTextMessageContent(
                            message_text="Aapne abhi tak koi quiz nahi banaya hai. Naya quiz banane ke liye bot me /newquiz likhein."
                        )
                    )
                )
            else:
                for quiz in user_quizzes:
                    quiz_id, title, description, timer = quiz
                    
                    # Total questions count fetch karein
                    cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
                    count_data = cursor.fetchone()
                    total_q = count_data[0] if count_data else 0
                    
                    time_display = f"{timer}s" if timer < 60 else f"{timer // 60}m"
                    escaped_title = escape_markdown(title)
                    escaped_desc = escape_markdown(description) if description else "No description"
                    
                    # List me se click karte hi ye text aur panel direct chat me share ho jayega
                    share_message_text = (
                        f"🎲 Quiz {escaped_title}\n\n"
                        f"💌 **Description:** {escaped_desc}\n"
                        f"🖋️ {total_q} questions · ⏱ {time_display}"
                    )
                    
                    start_private_url = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
                    start_group_url = f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}"
                    
                    inline_keyboard = [
                        [InlineKeyboardButton("Start quiz in Private Chat", url=start_private_url)],
                        [InlineKeyboardButton("Start quiz in Group", url=start_group_url)],
                        [InlineKeyboardButton("Share Quiz", switch_inline_query=f"quiz_{quiz_id}")]
                    ]
                    
                    # Short Display Popup for List
                    results.append(
                        InlineQueryResultArticle(
                            id=f"list_{quiz_id}",
                            title=f"🎲 Quiz {title}", 
                            description=f"⚡ {total_q} Qs   ·   ⏱ {time_display}", 
                            input_message_content=InputTextMessageContent(
                                message_text=share_message_text,
                                parse_mode="Markdown"
                            ),
                            reply_markup=InlineKeyboardMarkup(inline_keyboard)
                        )
                    )
            
            conn.close()
            await update.inline_query.answer(results, cache_time=0, is_personal=True)
            return

        # CASE 2: Single Quiz Share handler (`quiz_12` query trigger hone par)
        if query.startswith("quiz_"):
            # 🌟 FIX: String id ko extract karke safely int me convert kiya taaki SQL query NULL return na kare
            try:
                quiz_id = int(query.replace("quiz_", ""))
            except ValueError:
                conn.close()
                return  # Malformed input par safely exit karein
            
            cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
            quiz_data = cursor.fetchone()
            
            if quiz_data:
                title, description, timer = quiz_data
                
                cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
                count_data = cursor.fetchone()
                total_q = count_data[0] if count_data else 0
                conn.close()

                time_display = f"{timer}s" if timer < 60 else f"{timer // 60}m"
                escaped_title = escape_markdown(title)
                escaped_desc = escape_markdown(description) if description else "No description"
                
                share_message_text = (
                        f"🎲 Quiz {escaped_title}\n\n"
                        f"💌 **Description:** {escaped_desc}\n"
                        f"🖋️ {total_q} questions · ⏱ {time_display}"
                )
                
                start_private_url = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
                start_group_url = f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}"
                
                inline_keyboard = [
                    [InlineKeyboardButton("Start quiz in Private Chat", url=start_private_url)],
                    [InlineKeyboardButton("Start quiz in Group", url=start_group_url)],
                    [InlineKeyboardButton("Share Quiz", switch_inline_query=f"quiz_{quiz_id}")]
                ]
                
                results = [
                    InlineQueryResultArticle(
                        id=str(quiz_id),
                        title=f"🎲 Quiz {title}",
                        description=f"⚡ {total_q} Qs   ·   ⏱ {time_display}",
                        input_message_content=InputTextMessageContent(
                            message_text=share_message_text,
                            parse_mode="Markdown"
                        ),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard)
                    )
                ]
                await update.inline_query.answer(results, cache_time=0)
            else:
                conn.close()
                
    except Exception as e:
        logging.error(f"Error in inline_query_handler: {e}")
        
async def owner_status_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct text command /status for the Owner to view all groups using broadcast_groups table"""
    try:
        user_id = update.message.from_user.id
        
        # 🟢 .env se li gayi OWNER_ID se matching check
        if OWNER_ID is None or user_id != OWNER_ID:
            await update.message.reply_text("❌ Unauthorized! This command is only accessible by the bot owner.")
            return

        processing_msg = await update.message.reply_text("🔍 Fetching active groups from broadcast logs...")
        
        # 🟢 Connecting to database and reading from broadcast_groups table
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT chat_id FROM broadcast_groups")
            chat_rows = cursor.fetchall()
        except Exception as db_err:
            logging.error(f"Database error while reading broadcast_groups: {db_err}")
            chat_rows = []
        conn.close()

        if not chat_rows:
            await processing_msg.delete()
            await update.message.reply_text(
                "⚠️ *No groups found in broadcast logs!.*\n\n"
                "💡 *Reason:* Aapki `broadcast_groups` table abhi khali hai. Jab bot kisi group me save hoga ya broadcast me add hoga, tabhi yahan data dikhega."
            )
            return

        status_report = "📊 *Bot Active Groups Status Report*\n\n"
        group_count = 0

        for row in chat_rows:
            target_chat_id = row[0] # Fetching the chat_id from tuple
            
            try:
                # Live Telegram API lookup call
                chat_details = await context.bot.get_chat(chat_id=target_chat_id)
                group_name = chat_details.title
                
                try:
                    invite_link = chat_details.invite_link
                    if not invite_link:
                        # Bot ke paas invite links export karne ki permission honi chahiye
                        invite_link = await context.bot.export_chat_invite_link(chat_id=target_chat_id)
                except Exception:
                    invite_link = "No Link Permission 🚫"

                group_count += 1
                status_report += f"*{group_count}. 👥 Name:* {escape_markdown(group_name)}\n"
                status_report += f"🆔 *Chat ID:* `{target_chat_id}`\n"
                status_report += f"🔗 *Link:* {invite_link}\n"
                status_report += "━" * 15 + "\n"
            except Exception:
                # Agar bot group se remove ho chuka hai toh use skip karein
                continue

        await processing_msg.delete()

        if group_count == 0:
            await update.message.reply_text("⚠️ No active groups accessible. Bot might have been removed from old groups.")
        else:
            status_report += f"\n📉 *Total Active Groups:* {group_count}"
            await update.message.reply_text(text=status_report, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Error in owner_status_text_command: {e}")
        await update.message.reply_text("❌ Error generating groups status details.")
                    
#broadcast command handler
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast command for owner to send text, media, or stickers safely"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id  # Current Group ID check karne ke liye
    chat_type = update.effective_chat.type

    # 1. Check karein ki user owner hai ya nahi (Strict Owner Check)
    if OWNER_ID and user_id != OWNER_ID:
        await update.message.reply_text("👑 This command is only for the bot owner.")
        return

    # 2. Check karein ki command authorized group me hai YA owner ki private chat me hai
    is_support_group = (SUPPORT_GROUP_ID and chat_id == SUPPORT_GROUP_ID)
    is_private_chat = (chat_type == "private")

    if not (is_support_group or is_private_chat):
        await update.message.reply_text("❌ Ye command is group me allowed nahi hai.")
        return

    # Check karein ki kisi message par reply kiya gaya hai ya nahi
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kisi bhi text, photo, sticker ya media par reply karke `/broadcast` likhein.")
        return

    target_message = update.message.reply_to_message
    status_msg = await update.message.reply_text("📢 Quiz Bot Broadcast shuru ho raha hai...")

    # Database se active chats fetch karein
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM broadcast_users")
    users = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT chat_id FROM broadcast_groups")
    groups = [row[0] for row in cursor.fetchall()]
    conn.close()

    success_users, failed_users = 0, 0
    success_groups, failed_groups = 0, 0

    # 1. Private Users ko bheinjein
    for u_id in users:
        try:
            # copy_message sticker, media, text sab kuch original format me bina kisi tag ke bhejta hai
            await context.bot.copy_message(
                chat_id=u_id, 
                from_chat_id=target_message.chat_id, 
                message_id=target_message.message_id
            )
            success_users += 1
            await asyncio.sleep(0.1)  # FloodWait se bachne ke liye safe delay (0.05 se 0.1 kiya)
        except Exception:
            failed_users += 1

    # 2. Groups ko bheinjein
    for g_id in groups:
        try:
            await context.bot.copy_message(
                chat_id=g_id, 
                from_chat_id=target_message.chat_id, 
                message_id=target_message.message_id
            )
            success_groups += 1
            await asyncio.sleep(0.1) # Safe delay
        except Exception:
            failed_groups += 1

    # Final Report card display karein
    report = (
        "📊 Quiz Bot Broadcast Report:\n\n"
        "👤 Private Chats:\n"
        f"✅ Success: {success_users}\n"
        f"❌ Failed: {failed_users}\n\n"
        "👥 Groups:\n"
        f"✅ Success: {success_groups}\n"
        f"❌ Failed: {failed_groups}"
    )
    await status_msg.edit_text(report)
        
# =====================================================================

async def main():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN not found in environment variables!")
        return
    
    try:
        # 🌟 FIX: Database ko yahan initialize kiya taaki tables automatically ban jayein
        init_db()  
        
        # 🔥 GLOBAL TIMEOUT FIX: Saare parameters ko request_config ke andar hi handle kiya hai
        request_config = HTTPXRequest(
            connect_timeout=35.0,  # Max connection hold time
            read_timeout=45.0,     # Max wait time for incoming operations
            write_timeout=35.0     # Max delivery buffer time
        )
        
        # ✅ SYNTAX & CONFIG FIXED: Builder se dot(.) wale extra timeouts hata diye hain
        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .request(request_config)
            .build()
        )
        
        # 🔁 COMPREHENSIVE DUAL CONVERSATION ROUTER MAPS (Creation + Live Editing)
        new_quiz_handler = ConversationHandler(
            entry_points=[
                CommandHandler("newquiz", new_quiz_start),
                CallbackQueryHandler(new_quiz_start, pattern="^btn_newquiz$")
            ],
            states={
                TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
                DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc), CommandHandler("skip", receive_desc)],
                QUESTIONS: [CommandHandler("undo", handle_undo), CommandHandler("done", finish_quiz_creation), MessageHandler(filters.POLL, receive_poll)],
                
                # 🟢 PRE_MESSAGE state config for 2x /undo sequence mapping 
                PRE_MESSAGE: [
                    CommandHandler("undo", handle_undo),  # 👈 /undo handler commands structural lookup priority mapping
                    CommandHandler("skip", receive_pre_message),
                    MessageHandler(filters.POLL, receive_pre_message),
                    # 👈 Commands are filtered out so /undo doesn't transform into text stream payload
                    MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION) & ~filters.COMMAND, receive_pre_message)
                ],
                
                # 🟢 TIMER state me CallbackQueryHandler add kiya button input ke liye
                TIMER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timer_text),
                    CallbackQueryHandler(handle_timer_text, pattern="^timer_")  # 👈 Buttons capture karne ke liye yeh line jodi hai
                ],
                
                # 🔥 NEW NEGATIVE MARKING STATE INTEGRATED (CREATION FLOW):
                NEGATIVE: [
                    CallbackQueryHandler(handle_negative_selection, pattern="^neg_")
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )

        # 🔥 UPDATED EDIT FLOW CONVERSATION HANDLER
        quiz_edit_flow_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(edit_title_trigger, pattern="^edtitle_"),
                CallbackQueryHandler(edit_desc_trigger, pattern="^eddesc_"),
                CallbackQueryHandler(edit_timer_trigger, pattern="^edtime_"),
                CallbackQueryHandler(edit_negative_trigger, pattern="^edneg_"), # 👈 Nayi trigger line register ki hai
                CallbackQueryHandler(edit_pre_message_trigger, pattern="^editpre_"),
                CallbackQueryHandler(edit_explanation_trigger, pattern="^editexpl_")
            ],
            states={
                EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_title)],
                EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_desc)],
                EDIT_TIMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_timer)],
                # 🔥 NEW RESPONSE STATE FOR EDIT FLOW:
                EDIT_NEGATIVE: [CallbackQueryHandler(save_edited_negative, pattern="^updeneg_")],
                EDIT_QUESTION_PRE_MESSAGE: [MessageHandler(filters.TEXT, save_pre_message)],
                EDIT_QUESTION_EXPLANATION: [MessageHandler(filters.TEXT, save_explanation)]
            },
            fallbacks=[CommandHandler("cancel", cancel)]
        )
        
        # Registering core structures hooks
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("quizzes", quizzes_command))
        app.add_handler(CommandHandler("stop", stop_quiz))
        app.add_handler(CommandHandler("status", owner_status_text_command))
        
        app.add_handler(new_quiz_handler)
        app.add_handler(quiz_edit_flow_handler)

        # Core system triggers binding maps
        app.add_handler(CallbackQueryHandler(view_my_quizzes, pattern="^btn_viewquizzes$"))
        app.add_handler(CallbackQueryHandler(handle_back_main, pattern="^back_main$"))
        app.add_handler(CallbackQueryHandler(handle_view_quiz_callback, pattern="^viewq_"))
        
        app.add_handler(CallbackQueryHandler(handle_ready_click, pattern="^ready_"))
        app.add_handler(CallbackQueryHandler(handle_start_private, pattern="^startprivate_"))
        app.add_handler(CallbackQueryHandler(handle_confirm_private, pattern="^confirm_private_"))
        app.add_handler(CallbackQueryHandler(handle_quiz_status, pattern="^status_"))
        app.add_handler(CallbackQueryHandler(edit_quiz_menu, pattern="^edit_"))
        app.add_handler(CallbackQueryHandler(back_to_summary, pattern="^backto_"))
        app.add_handler(CallbackQueryHandler(edit_question_trigger, pattern="^edquestion_"))
        app.add_handler(CallbackQueryHandler(handle_question_detail, pattern="^editq_"))
        app.add_handler(CallbackQueryHandler(handle_delete_question, pattern="^delq_"))
        app.add_handler(CallbackQueryHandler(confirm_delete_question, pattern="^confirmdel_"))
        app.add_handler(CommandHandler("broadcast", broadcast_command))
        
        # 🔴 Quiz pause/resume handlers
        app.add_handler(CallbackQueryHandler(handle_pause_quiz, pattern="^pausequiz_"))
        app.add_handler(CallbackQueryHandler(handle_stop_quiz_from_pause, pattern="^stopquiz_"))
        
        app.add_handler(PollAnswerHandler(track_poll_answers))
        app.add_handler(InlineQueryHandler(inline_query_handler))
        
        # 🚀 BOT RUN/POLLING INITIALIZATION
        logging.info("Starting Quiz Bot polling...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Bot ko running state me rakhne ke liye infinite event wait loop
        await asyncio.Event().wait()

    except Exception as e:
        logging.error(f"Critical error in main loop: {e}")
        
        
# 🛑 EXECUTION LOOPS CLOSURE:
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot execution stopped clean.")
        
