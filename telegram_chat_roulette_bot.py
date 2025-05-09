import logging
import os
import time
from pathlib import Path
import json
from collections import deque
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# --- Configuration & Constants ---
ADMIN_ID = 5413055151
DATA_DIR = Path(os.getenv('DATA_DIR', '/app/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "data.json"

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Global State ---
search_queue: deque[int] = deque()
active_chats: dict[int, int] = {}
nicknames: dict[int, dict] = {}
ratings: dict[int, dict] = {}
reports: dict[int, int] = {}

# Conversation states
SET_NICKNAME, CHATTING, SET_GENDER, SET_PREFERRED_GENDER = range(4)

# --- Data Persistence ---

def load_data() -> None:
    global search_queue, active_chats, nicknames, ratings, reports
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            search_queue = deque(data.get('search_queue', []))
            active_chats = {int(k): int(v) for k, v in data.get('active_chats', {}).items()}
            nicknames = {int(k): v for k, v in data.get('nicknames', {}).items()}
            ratings = {int(k): v for k, v in data.get('ratings', {}).items()}
            reports = {int(k): v for k, v in data.get('reports', {}).items()}
            logger.info(f"Loaded data from {DATA_FILE}")
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            search_queue.clear()
            active_chats.clear()
            nicknames.clear()
            ratings.clear()
            reports.clear()
    else:
        logger.info(f"Data file {DATA_FILE} not found; starting fresh.")


def save_data() -> None:
    payload = {
        'search_queue': list(search_queue),
        'active_chats': {str(k): v for k, v in active_chats.items()},
        'nicknames': {str(k): v for k, v in nicknames.items()},
        'ratings': {str(k): v for k, v in ratings.items()},
        'reports': {str(k): v for k, v in reports.items()},
    }
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Saved data to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save data: {e}")

# --- Keyboards ---
def get_main_keyboard(user_id=None, message_id=None):
    keyboard = [
        [InlineKeyboardButton("🔍 Найти собеседника", callback_data="find")],
        [InlineKeyboardButton("🔍 Поиск по полу", callback_data="find_by_gender")],
        [InlineKeyboardButton("🚪 Завершить чат", callback_data="end")],
        [InlineKeyboardButton("✏️ Сменить псевдоним", callback_data="set_nickname")],
        [InlineKeyboardButton("⚧ Изменить пол", callback_data="set_gender")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Мужской", callback_data="gender_male"),
         InlineKeyboardButton("👩 Женский", callback_data="gender_female"),
         InlineKeyboardButton("❓ Не указан", callback_data="gender_none")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_gender")],
    ])

def get_preferred_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Мужчин", callback_data="pref_gender_male"),
         InlineKeyboardButton("👩 Женщин", callback_data="pref_gender_female"),
         InlineKeyboardButton("🌐 Любой", callback_data="pref_gender_any")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_pref_gender")],
    ])

def get_rating_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    stars = [[InlineKeyboardButton('⭐'*i, callback_data=f'rate_{partner_id}_{i}') for i in range(1, 6)]]
    report = [[InlineKeyboardButton("⚠️ Пожаловаться", callback_data=f"report_{partner_id}")]]
    return InlineKeyboardMarkup(stars + report)

# --- Matching Logic ---
async def check_queue(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data if hasattr(context.job, 'data') else None
    if not user_id or user_id not in search_queue:
        return
    queue_copy = list(search_queue)
    for i, u1 in enumerate(queue_copy):
        if u1 not in search_queue:
            continue
        g1 = nicknames.get(u1, {})
        for u2 in queue_copy[i + 1:]:
            if u2 not in search_queue:
                continue
            g2 = nicknames.get(u2, {})
            ok1 = g1.get('preferred_gender', 'Любой') in ('Любой', g2.get('gender', 'Не указан'))
            ok2 = g2.get('preferred_gender', 'Любой') in ('Любой', g1.get('gender', 'Не указан'))
            if ok1 and ok2:
                search_queue.remove(u1)
                search_queue.remove(u2)
                active_chats[u1] = u2
                active_chats[u2] = u1
                await context.bot.send_message(u1, f"🎉 Чат с {html.escape(nicknames[u2]['nickname'])} начат!", reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
                await context.bot.send_message(u2, f"🎉 Чат с {html.escape(nicknames[u1]['nickname'])} начат!", reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
                return

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    nicknames.setdefault(u.id, {'nickname': f"Аноним_{u.id % 1000}", 'gender': 'Не указан', 'preferred_gender': 'Любой'})

    # Удалим предыдущее сообщение, если это возможно (inline)
    if update.message:
        await update.message.reply_text(
            f"✨ Привет, {html.escape(u.first_name)}! Добро пожаловать в чат-рулетку.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            f"✨ Привет, {html.escape(u.first_name)}! Добро пожаловать в чат-рулетку.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    return CHATTING

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cmd = query.data

    # Удаляем старое сообщение, чтобы интерфейс был чище
    try:
        await query.message.delete()
    except:
        pass

    if cmd == 'find':
        if user_id in active_chats:
            await context.bot.send_message(user_id, "⚠️ Уже в чате!", reply_markup=get_main_keyboard())
        elif user_id not in search_queue:
            search_queue.append(user_id)
            await context.bot.send_message(user_id, f"🔎 Ищем... очередь: {len(search_queue)}", reply_markup=get_main_keyboard())
            context.job_queue.run_repeating(check_queue, interval=5, first=0, data=user_id, name=f"queue_{user_id}")
        return CHATTING

    if cmd == 'find_by_gender':
        await context.bot.send_message(user_id, "🔍 Выбери пол для поиска:", reply_markup=get_preferred_gender_keyboard())
        return SET_PREFERRED_GENDER

    if cmd == 'end':
        if user_id in active_chats:
            partner = active_chats.pop(user_id)
            active_chats.pop(partner, None)
            try:
                context.job_queue.get_jobs_by_name(f"queue_{user_id}")[0].schedule_removal()
            except IndexError:
                pass
            try:
                context.job_queue.get_jobs_by_name(f"queue_{partner}")[0].schedule_removal()
            except IndexError:
                pass
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🚪 Чат завершён.\n"
                    f"Как тебе {html.escape(nicknames.get(partner, {}).get('nickname', 'партнёр'))}?"
                ),
                reply_markup=get_rating_keyboard(partner)
            )
            await context.bot.send_message(
                chat_id=partner,
                text=(
                    f"🚪 Твой собеседник вышел из чата.\n"
                    f"Как тебе {html.escape(nicknames.get(user_id, {}).get('nickname', 'партнёр'))}?"
                ),
                reply_markup=get_rating_keyboard(user_id)
            )
        elif user_id in search_queue:
            search_queue.remove(user_id)
            try:
                context.job_queue.get_jobs_by_name(f"queue_{user_id}")[0].schedule_removal()
            except IndexError:
                pass
            await context.bot.send_message(user_id, "🔍 Поиск отменён.", reply_markup=get_main_keyboard())
        else:
            await context.bot.send_message(user_id, "🤔 Ты не в чате.", reply_markup=get_main_keyboard())
        return CHATTING

    if cmd == 'set_nickname':
        await context.bot.send_message(user_id, "✏️ Введи псевдоним:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]]))
        return SET_NICKNAME

    if cmd == 'set_gender':
        await context.bot.send_message(user_id, "⚧ Выбери свой пол:", reply_markup=get_gender_keyboard())
        return SET_GENDER

    return CHATTING

async def receive_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    nickname = update.message.text.strip()
    if 1 <= len(nickname) <= 20:
        nicknames[user_id]['nickname'] = nickname
        await update.message.reply_text(
            f"✅ Псевдоним установлен: {html.escape(nickname)}",
            reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML
        )
        return CHATTING
    await update.message.reply_text(
        "⚠️ Псевдоним должен быть от 1 до 20 символов."
    )
    return SET_NICKNAME

async def set_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = update.callback_query.data
    uid = update.callback_query.from_user.id
    mapping = {
        'gender_male': 'Мужской',
        'gender_female': 'Женский',
        'gender_none': 'Не указан'
    }
    gender = mapping.get(data)
    if gender:
        nicknames[uid]['gender'] = gender
        await update.callback_query.edit_message_text(
            f"✅ Пол установлен: {gender}",
            reply_markup=get_main_keyboard()
        )
    return CHATTING

async def set_preferred_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = update.callback_query.data
    uid = update.callback_query.from_user.id
    pg = {'pref_gender_male':'Мужской','pref_gender_female':'Женский','pref_gender_any':'Любой'}.get(data)
    if pg:
        nicknames[uid]['preferred_gender'] = pg
        await update.callback_query.edit_message_text(
            f"✅ Ищу: {pg}", reply_markup=get_main_keyboard()
        )
        return CHATTING
    return CHATTING

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid=update.effective_user.id
    if uid in active_chats:
        pid=active_chats[uid];msg=update.message
        # text
        if msg.text:
            await context.bot.send_message(pid,f"💬 {html.escape(nicknames[uid]['nickname'])}: {html.escape(msg.text)}")
        # photo
        elif msg.photo:
            file=msg.photo[-1].file_id
            await context.bot.send_photo(pid,file,caption=f"📸 {html.escape(nicknames[uid]['nickname'])}")
        # video
        elif msg.video:
            await context.bot.send_video(pid,msg.video.file_id,caption=f"🎥 {html.escape(nicknames[uid]['nickname'])}")
        # document
        elif msg.document:
            await context.bot.send_document(pid,msg.document.file_id,caption=f"📄 {html.escape(nicknames[uid]['nickname'])}")
        # audio
        elif msg.audio:
            await context.bot.send_audio(pid,msg.audio.file_id,caption=f"🎵 {html.escape(nicknames[uid]['nickname'])}")
        # voice
        elif msg.voice:
            await context.bot.send_voice(pid,msg.voice.file_id,caption=f"🎤 {html.escape(nicknames[uid]['nickname'])}")
        # video note
        elif msg.video_note:
            await context.bot.send_video_note(pid,msg.video_note.file_id)
        else:
            await context.bot.send_message(pid,"[Неподдерживаемый тип сообщения]")
    else:
        await update.message.reply_text("🤔 Не в чате.",reply_markup=get_main_keyboard())
    return CHATTING

async def handle_rating_or_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    try:
        parts = query.data.split('_')
        action = parts[0]

        if action == 'rate' and len(parts) == 3:
            partner_id = int(parts[1])
            rating = int(parts[2])
            if partner_id not in ratings:
                ratings[partner_id] = {'total': 0, 'count': 0}
            ratings[partner_id]['total'] += rating
            ratings[partner_id]['count'] += 1
            avg = ratings[partner_id]['total'] / ratings[partner_id]['count']
            await query.edit_message_text(
                text=f"⭐ Спасибо за оценку! Текущий средний рейтинг пользователя: {avg:.1f}"
            )

        elif action == 'report' and len(parts) == 2:
            partner_id = int(parts[1])
            reports[partner_id] = reports.get(partner_id, 0) + 1
            await query.edit_message_text(
                text=f"⚠️ Жалоба отправлена. Общее число жалоб: {reports[partner_id]}"
            )
            if reports[partner_id] >= 3:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text="🚫 На ваш аккаунт поступило несколько жалоб. Пожалуйста, соблюдайте правила общения."
                )

    except Exception as e:
        logger.error(f"Ошибка в обработке оценки/жалобы: {e}")
        await query.edit_message_text("⚠️ Произошла ошибка при обработке запроса.")

    return CHATTING

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info(f"Команда /admin вызвана пользователем {user_id}")

    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 У тебя нет доступа к админ-панели.")
        return

    total_users = len(nicknames)
    active = len(active_chats) // 2
    searching = len(search_queue)

    reported_lines = []
    for uid, count in reports.items():
        if count >= 3:
            nickname = nicknames.get(uid, {}).get("nickname", "Неизвестный")
            reported_lines.append(f"• {nickname} (ID: {uid}) — {count} жалоб")

    reported_text = "\n".join(reported_lines) if reported_lines else "Нет пользователей с 3+ жалобами."

    rating_entries = []
    for uid, data in ratings.items():
        if data["count"] >= 3:
            avg = round(data["total"] / data["count"], 2)
            nickname = nicknames.get(uid, {}).get("nickname", "Неизвестный")
            rating_entries.append((avg, data["count"], nickname, uid))

    top_rated = sorted(rating_entries, reverse=True)[:5]
    if top_rated:
        top_rating_text = "\n".join([
            f"{i+1}. {nickname} (ID: {uid}) — {avg} ⭐ ({count} оценок)"
            for i, (avg, count, nickname, uid) in enumerate(top_rated)
        ])
    else:
        top_rating_text = "Нет пользователей с рейтингом."

    text = (
        f"<b>📊 Админ-панель</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"💬 В активных чатах: <b>{active}</b>\n"
        f"🔎 В поиске: <b>{searching}</b>\n\n"
        f"⭐ <b>Топ-5 по рейтингу:</b>\n{top_rating_text}\n\n"
        f"⚠️ <b>Жалобы (3+):</b>\n{reported_text}"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# --- Error Handler ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}")

# --- Main ---
def main() -> None:
    load_data()
    token = os.getenv('BOT_TOKEN')
    if not token:
        logger.error("TOKEN не указан")
        return
    app = ApplicationBuilder().token(token).build()

    # Восстановим ADMIN_ID явно:
    global ADMIN_ID
    ADMIN_ID = int(os.getenv("ADMIN_ID", "5413055151"))

    # Задачи
    app.job_queue.run_repeating(lambda ctx: save_data(), interval=300, first=300)

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SET_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_nickname)],
            SET_GENDER: [CallbackQueryHandler(set_gender_choice, pattern='^gender_')],
            SET_PREFERRED_GENDER: [CallbackQueryHandler(set_preferred_gender_choice, pattern='^pref_gender_')],
            CHATTING: [
                CallbackQueryHandler(button, pattern="^(find|find_by_gender|end|set_nickname|set_gender)$"),
                CallbackQueryHandler(handle_rating_or_report, pattern="^(rate|report)_[0-9]+(_[1-5])?$"),
                MessageHandler(filters.ALL, handle_message)
            ]
        },
        fallbacks=[CommandHandler('start', start)]
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_error_handler(lambda u, c: logger.error(c.error))
    app.run_polling()

if __name__ == '__main__':
    main()
