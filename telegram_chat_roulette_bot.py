import logging
import os
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import random
from collections import deque
import asyncio
import json
from pathlib import Path

# Включение логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Определение пути к данным
DATA_DIR = Path("/app/data")  # Основной путь для тома
if not DATA_DIR.exists():
    # Если /app/data не доступен, используем путь из логов Railway
    DATA_DIR = Path("/var/lib/containers/railwayapp/bind-mounts/b7001a8b-8c39-4357-b4e4-216af86b3aa9/vol_g8vjvp7jakq2vjtl")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "data.json"

# Создаём директорию, если она не существует
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Глобальные переменные
search_queue = deque()
active_chats = {}
nicknames = {}
search_messages = {}
search_timeouts = {}
temp_messages = {}
ratings = {}  # {user_id: {"total": int, "count": int}}
reports = {}  # {user_id: count}

# Состояния для ConversationHandler
SET_NICKNAME, CHATTING, SET_GENDER, SET_PREFERRED_GENDER = range(4)

# --- Сохранение и загрузка данных ---
def load_data():
    global search_queue, active_chats, nicknames, ratings, reports
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                search_queue = deque(data.get("search_queue", []))
                active_chats = {int(k): int(v) for k, v in data.get("active_chats", {}).items()}
                nicknames = {int(k): v for k, v in data.get("nicknames", {}).items()}
                ratings = {int(k): v for k, v in data.get("ratings", {}).items()}
                reports = {int(k): v for k, v in data.get("reports", {}).items()}
            logger.info(f"Данные успешно загружены из {DATA_FILE}")
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
            search_queue = deque()
            active_chats = {}
            nicknames = {}
            ratings = {}
            reports = {}
    else:
        logger.info(f"Файл {DATA_FILE} не существует, инициализация пустых данных")

# Вызываем загрузку данных при старте
load_data()

def save_data(data):
    serializable_data = {
        "search_queue": list(data["search_queue"]),
        "active_chats": {str(k): v for k, v in data["active_chats"].items()},
        "nicknames": {str(k): v for k, v in data["nicknames"].items()},
        "ratings": {str(k): v for k, v in data["ratings"].items()},
        "reports": {str(k): v for k, v in data["reports"].items()},
    }
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(serializable_data, f, indent=2)
        logger.info(f"Данные успешно сохранены в {DATA_FILE}")
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

async def save_data_periodic(context: ContextTypes.DEFAULT_TYPE):
    save_data(context.job.data)
    logger.info("Данные успешно сохранены (периодическое сохранение).")

# --- Функции клавиатур ---
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔍 Найти собеседника", callback_data="find")],
        [InlineKeyboardButton("🔍 Поиск по полу", callback_data="find_by_gender")],
        [InlineKeyboardButton("🚪 Завершить чат", callback_data="end")],
        [InlineKeyboardButton("✏️ Сменить псевдоним", callback_data="set_nickname")],
        [InlineKeyboardButton("⚧ Изменить пол", callback_data="set_gender")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_gender_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("👨 Мужской", callback_data="gender_male"),
            InlineKeyboardButton("👩 Женский", callback_data="gender_female"),
            InlineKeyboardButton("❓ Не указан", callback_data="gender_none"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_gender")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_preferred_gender_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("👨 Мужчин", callback_data="pref_gender_male"),
            InlineKeyboardButton("👩 Женщин", callback_data="pref_gender_female"),
            InlineKeyboardButton("🌐 Любой", callback_data="pref_gender_any"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_pref_gender")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_rating_keyboard(partner_id):
    keyboard = [
        [
            InlineKeyboardButton("⭐", callback_data=f"rate_{partner_id}_1"),
            InlineKeyboardButton("⭐⭐", callback_data=f"rate_{partner_id}_2"),
            InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_{partner_id}_3"),
            InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_{partner_id}_4"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_{partner_id}_5"),
        ],
        [InlineKeyboardButton("⚠️ Пожаловаться", callback_data=f"report_{partner_id}")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Основной функционал ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"User {user.id} sent /start command")
    message = await update.message.reply_text(
        f"✨ Привет, {user.first_name}! Добро пожаловать в анонимную чат-рулетку 2025! 🎉\n"
        "Сначала придумай себе псевдоним для анонимного общения.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Задать псевдоним", callback_data="set_nickname")]
        ])
    )
    temp_messages[user.id] = message.message_id
    return SET_NICKNAME

async def set_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f"User {user_id} requested to set nickname")
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    message = await query.message.reply_text(
        "💬 Введи свой псевдоним (до 20 символов):",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ])
    )
    temp_messages[user_id] = message.message_id
    return SET_NICKNAME

async def receive_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    nickname = update.message.text.strip()
    logger.info(f"User {user_id} submitted nickname: {nickname}")
    if len(nickname) > 20:
        if user_id in temp_messages:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        message = await update.message.reply_text(
            "⚠️ Псевдоним слишком длинный! Попробуй до 20 символов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        temp_messages[user_id] = message.message_id
        return SET_NICKNAME
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    nicknames[user_id] = {"nickname": nickname, "gender": "Не указан", "preferred_gender": "Любой"}
    await update.message.reply_text(
        f"✅ Псевдоним '{nickname}' установлен!\n"
        "Теперь ты готов к анонимному чату. Нажми 'Найти собеседника' 🚀",
        reply_markup=get_main_keyboard()
    )
    return CHATTING

async def set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f"User {user_id} requested to set gender")
    message = await query.message.reply_text(
        "⚧ Выбери свой пол:",
        reply_markup=get_gender_keyboard()
    )
    temp_messages[user_id] = message.message_id
    return SET_GENDER

async def set_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    if query.data == "gender_male":
        nicknames[user_id]["gender"] = "Мужской"
        await query.message.reply_text(
            "✅ Пол установлен: 👨 Мужской",
            reply_markup=get_main_keyboard()
        )
    elif query.data == "gender_female":
        nicknames[user_id]["gender"] = "Женский"
        await query.message.reply_text(
            "✅ Пол установлен: 👩 Женский",
            reply_markup=get_main_keyboard()
        )
    elif query.data == "gender_none":
        nicknames[user_id]["gender"] = "Не указан"
        await query.message.reply_text(
            "✅ Пол установлен: ❓ Не указан",
            reply_markup=get_main_keyboard()
        )
    return CHATTING

async def set_preferred_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f"User {user_id} requested to set preferred gender")
    message = await query.message.reply_text(
        "🔍 Кого ты хочешь найти?",
        reply_markup=get_preferred_gender_keyboard()
    )
    temp_messages[user_id] = message.message_id
    return SET_PREFERRED_GENDER

async def set_preferred_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    if query.data == "pref_gender_male":
        nicknames[user_id]["preferred_gender"] = "Мужской"
        await query.message.reply_text(
            "✅ Будем искать: 👨 Мужчин",
            reply_markup=get_main_keyboard()
        )
    elif query.data == "pref_gender_female":
        nicknames[user_id]["preferred_gender"] = "Женский"
        await query.message.reply_text(
            "✅ Будем искать: 👩 Женщин",
            reply_markup=get_main_keyboard()
        )
    elif query.data == "pref_gender_any":
        nicknames[user_id]["preferred_gender"] = "Любой"
        await query.message.reply_text(
            "✅ Будем искать: 🌐 Любой пол",
            reply_markup=get_main_keyboard()
        )
    if user_id in active_chats:
        await query.message.reply_text(
            "⚠️ Ты уже в чате! Заверши его, чтобы начать новый.",
            reply_markup=get_main_keyboard()
        )
        return CHATTING
    if user_id in search_queue:
        await query.message.reply_text(
            f"⏳ Ты уже ищешь! В очереди: {len(search_queue)} человек...",
            reply_markup=get_main_keyboard()
        )
        return CHATTING
    search_queue.append(user_id)
    message = await query.message.reply_text(
        f"🔎 Ищем тебе собеседника... (в очереди: {len(search_queue)} человек)",
        reply_markup=get_main_keyboard()
    )
    search_messages[user_id] = message.message_id
    if context.job_queue:
        context.job_queue.run_once(timeout_search, 60, data=user_id)
        context.job_queue.run_repeating(check_queue, interval=5, first=0, data=user_id)
    else:
        logger.error("JobQueue не инициализирован. Функции поиска не будут работать.")
    return CHATTING

async def cancel_preferred_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f"User {user_id} canceled preferred gender setup")
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    await query.message.reply_text(
        "🔍 Поиск по полу отменён.",
        reply_markup=get_main_keyboard()
    )
    return CHATTING

async def cancel_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f"User {user_id} canceled gender setup")
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    await query.message.reply_text(
        "⚧ Изменение пола отменено.",
        reply_markup=get_main_keyboard()
    )
    return CHATTING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    if user_id in temp_messages:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=temp_messages[user_id])
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {temp_messages[user_id]} для {user_id}: {e}")
        del temp_messages[user_id]
    nicknames[user_id] = {"nickname": f"Аноним_{user_id % 1000}", "gender": "Не указан", "preferred_gender": "Любой"}
    logger.info(f"User {user_id} canceled nickname setup, using: {nicknames[user_id]['nickname']}")
    await query.message.reply_text(
        f"✨ Псевдоним не задан, используем: {nicknames[user_id]['nickname']}.\n"
        "Готов к чату? Нажми 'Найти собеседника' 🚀",
        reply_markup=get_main_keyboard()
    )
    return CHATTING

async def timeout_search(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data
    if user_id in search_queue:
        search_queue.remove(user_id)
        if user_id in search_messages:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=search_messages[user_id])
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение {search_messages[user_id]} для {user_id}: {e}")
            del search_messages[user_id]
        await context.bot.send_message(
            chat_id=user_id,
            text="⏳ Собеседник не найден. Попробуй позже!",
            reply_markup=get_main_keyboard()
        )

async def check_queue(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if len(search_queue) < 2:
        logger.info("Not enough users in queue")
        return

    users_to_check = list(search_queue)
    for i, user1 in enumerate(users_to_check):
        if user1 not in search_queue:
            continue
        user1_data = nicknames.get(user1, {"gender": "Не указан", "preferred_gender": "Любой"})
        for user2 in users_to_check[i+1:]:
            if user2 not in search_queue or user2 == user1:
                continue
            user2_data = nicknames.get(user2, {"gender": "Не указан", "preferred_gender": "Любой"})
            gender_match_1 = user1_data["preferred_gender"] == "Любой" or user1_data["preferred_gender"] == user2_data["gender"]
            gender_match_2 = user2_data["preferred_gender"] == "Любой" or user2_data["preferred_gender"] == user1_data["gender"]
            if gender_match_1 and gender_match_2:
                search_queue.remove(user1)
                search_queue.remove(user2)
                active_chats[user1] = user2
                active_chats[user2] = user1
                if user1 in search_messages:
                    try:
                        await context.bot.delete_message(chat_id=user1, message_id=search_messages[user1])
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение {search_messages[user1]} для {user1}: {e}")
                    del search_messages[user1]
                if user2 in search_messages:
                    try:
                        await context.bot.delete_message(chat_id=user2, message_id=search_messages[user2])
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение {search_messages[user2]} для {user2}: {e}")
                    del search_messages[user2]
                gender1 = f" (👨)" if user1_data["gender"] == "Мужской" else f" (👩)" if user1_data["gender"] == "Женский" else ""
                gender2 = f" (👨)" if user2_data["gender"] == "Мужской" else f" (👩)" if user2_data["gender"] == "Женский" else ""
                await context.bot.send_message(chat_id=user1, text=f"🎉 Чат с {nicknames[user2]['nickname']}{gender2} начат!", reply_markup=get_main_keyboard())
                await context.bot.send_message(chat_id=user2, text=f"🎉 Чат с {nicknames[user1]['nickname']}{gender1} начат!", reply_markup=get_main_keyboard())
                logger.info(f"✅ Connected user {user1} with {user2}")
                job.schedule_removal()
                return

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    logger.info(f"User {user_id} pressed button: {query.data}")
    await query.answer()

    if query.data == "find":
        if user_id in active_chats:
            await query.message.reply_text(
                "⚠️ Ты уже в чате! Заверши его, чтобы начать новый.",
                reply_markup=get_main_keyboard()
            )
            return
        if user_id in search_queue:
            await query.message.reply_text(
                f"⏳ Ты уже ищешь! В очереди: {len(search_queue)} человек...",
                reply_markup=get_main_keyboard()
            )
            return
        search_queue.append(user_id)
        message = await query.message.reply_text(
            f"🔎 Ищем тебе собеседника... (в очереди: {len(search_queue)} человек)",
            reply_markup=get_main_keyboard()
        )
        search_messages[user_id] = message.message_id
        if context.job_queue:
            context.job_queue.run_once(timeout_search, 60, data=user_id)
            context.job_queue.run_repeating(check_queue, interval=5, first=0, data=user_id)
        else:
            logger.error("JobQueue не инициализирован. Функции поиска не будут работать.")

    elif query.data == "find_by_gender":
        await query.message.reply_text(
            "🔍 Кого ты хочешь найти?",
            reply_markup=get_preferred_gender_keyboard()
        )
        return SET_PREFERRED_GENDER

    elif query.data == "end":
        if user_id not in active_chats:
            if user_id in search_queue:
                search_queue.remove(user_id)
                if user_id in search_messages:
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=search_messages[user_id])
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение {search_messages[user_id]} для {user_id}: {e}")
                    del search_messages[user_id]
                await query.message.reply_text(
                    "🔍 Поиск остановлен. Попробуй снова?",
                    reply_markup=get_main_keyboard()
                )
            else:
                await query.message.reply_text(
                    "🤔 Ты не в чате и не в поиске!", reply_markup=get_main_keyboard()
                )
            return

        partner_id = active_chats[user_id]
        del active_chats[user_id]
        del active_chats[partner_id]

        await context.bot.send_message(
            chat_id=user_id,
            text=f"🚪 Чат с {nicknames.get(partner_id, {}).get('nickname', 'Неизвестный')} завершен.\nКак тебе этот собеседник? Оцени:",
            reply_markup=get_rating_keyboard(partner_id)
        )
        await context.bot.send_message(
            chat_id=partner_id,
            text=f"🚪 Чат с {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')} завершен.\nКак тебе этот собеседник? Оцени:",
            reply_markup=get_rating_keyboard(user_id)
        )

    elif query.data == "set_nickname":
        await query.message.reply_text(
            "💬 Введи новый псевдоним (до 20 символов):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ])
        )
        return SET_NICKNAME

    elif query.data == "set_gender":
        await query.message.reply_text(
            "⚧ Выбери свой пол:",
            reply_markup=get_gender_keyboard()
        )
        return SET_GENDER

async def handle_rating_or_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    logger.info(f'Processing callback for user {user_id}: {query.data}')

    data = query.data.split('_')
    action = data[0]
    logger.info(f'Action detected: {action}')

    if action == 'rate':
        _, partner_id_str, rating_str = data
        try:
            partner_id = int(partner_id_str)
            rating = int(rating_str)
            logger.info(f'Rating {rating} for partner {partner_id}')

            if partner_id not in ratings:
                ratings[partner_id] = {'total': 0, 'count': 0}
            ratings[partner_id]['total'] += rating
            ratings[partner_id]['count'] += 1

            avg_rating = round(ratings[partner_id]['total'] / ratings[partner_id]['count'], 1)
            await context.bot.send_message(
                chat_id=user_id,
                text=f'✅ Спасибо за оценку!\nСредний рейтинг этого пользователя: {avg_rating} ⭐'
            )
            logger.info(f'Rating updated for {partner_id}, new average: {avg_rating}')
        except (ValueError, IndexError) as e:
            logger.error(f'Error processing rating: {e}')

    elif action == 'report':
        _, partner_id_str = data
        try:
            partner_id = int(partner_id_str)
            logger.info(f'Reporting user {partner_id}')

            if partner_id not in reports:
                reports[partner_id] = 0
            reports[partner_id] += 1

            report_count = reports[partner_id]
            await context.bot.send_message(
                chat_id=user_id,
                text=f'⚠️ Спасибо за жалобу!\nЭтот пользователь уже получил {report_count} жалоб(у/и).'
            )
            logger.info(f'Report count for {partner_id} updated to {report_count}')
            if report_count >= 3:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text='🚫 Тебе было отправлено несколько жалоб. Пожалуйста, соблюдай правила общения.'
                )
                logger.info(f'Warning sent to {partner_id} due to 3+ reports')
        except (ValueError, IndexError) as e:
            logger.error(f'Error processing report: {e}')

    data_to_save = {
        'search_queue': search_queue,
        'active_chats': active_chats,
        'nicknames': nicknames,
        'ratings': ratings,
        'reports': reports,
    }
    save_data(data_to_save)
    logger.info('Data saved successfully')
    return CHATTING

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} sent message or media")
    if user_id in active_chats:
        partner_id = active_chats[user_id]
        message = update.message

        # Обработка текста
        if message.text:
            await context.bot.send_message(
                chat_id=partner_id,
                text=f"💬 {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')}: {message.text}"
            )
            logger.info(f"Text message forwarded to {partner_id}")

        # Обработка фото
        elif message.photo:
            photo = message.photo[-1]  # Берем фото с наивысшим качеством
            await context.bot.send_photo(
                chat_id=partner_id,
                photo=photo.file_id,
                caption=f"📸 {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')}"
            )
            logger.info(f"Photo forwarded to {partner_id}")

        # Обработка видео
        elif message.video:
            await context.bot.send_video(
                chat_id=partner_id,
                video=message.video.file_id,
                caption=f"🎥 {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')}"
            )
            logger.info(f"Video forwarded to {partner_id}")

        # Обработка аудио
        elif message.audio:
            await context.bot.send_audio(
                chat_id=partner_id,
                audio=message.audio.file_id,
                caption=f"🎵 {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')}"
            )
            logger.info(f"Audio forwarded to {partner_id}")

        # Обработка документов
        elif message.document:
            await context.bot.send_document(
                chat_id=partner_id,
                document=message.document.file_id,
                caption=f"📄 {nicknames.get(user_id, {}).get('nickname', 'Неизвестный')}"
            )
            logger.info(f"Document forwarded to {partner_id}")

    else:
        await update.message.reply_text(
            "🤔 Ты не в чате! Нажми 'Найти собеседника'.",
            reply_markup=get_main_keyboard()
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error_msg = str(context.error).lower()
    logger.error(f"Update {update} caused error {context.error}")
    if "terminated by other getupdates request" in error_msg or "conflict" in error_msg:
        logger.warning("Обнаружен конфликт getUpdates. Ожидание перед перезапуском...")
        return  # Избегаем поднятия исключения, чтобы не вызвать цикл
    else:
        logger.error(f"Необработанная ошибка: {context.error}")

def main():
    # Получаем токен из переменной окружения
    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        logger.error("Переменная окружения BOT_TOKEN не установлена. Укажите токен бота.")
        return

    max_retries = 5
    retry_delay = 10  # Задержка между попытками в секундах

    for attempt in range(max_retries):
        try:
            application = ApplicationBuilder().token(bot_token).build()

            conv_handler = ConversationHandler(
                entry_points=[CommandHandler('start', start)],
                states={
                    SET_NICKNAME: [
                        CallbackQueryHandler(set_nickname, pattern='set_nickname'),
                        CallbackQueryHandler(cancel, pattern='cancel'),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_nickname),
                    ],
                    SET_GENDER: [
                        CallbackQueryHandler(set_gender_choice, pattern='^gender_(male|female|none)$'),
                        CallbackQueryHandler(cancel_gender, pattern='cancel_gender'),
                    ],
                    SET_PREFERRED_GENDER: [
                        CallbackQueryHandler(set_preferred_gender_choice, pattern='^pref_gender_(male|female|any)$'),
                        CallbackQueryHandler(cancel_preferred_gender, pattern='cancel_pref_gender'),
                    ],
                    CHATTING: [
                        CallbackQueryHandler(handle_rating_or_report, pattern='^(rate|report)_'),
                        CallbackQueryHandler(button),
                        MessageHandler(filters.TEXT & ~filters.COMMAND | filters.Photo | filters.Video | filters.Audio | filters.Document, handle_message),
                    ],
                },
                fallbacks=[],
            )

            application.add_handler(conv_handler)
            application.add_error_handler(error_handler)

            # Проверка доступности JobQueue перед использованием
            if application.job_queue:
                application.job_queue.run_repeating(save_data_periodic, interval=300, first=0, data={
                    "search_queue": search_queue,
                    "active_chats": active_chats,
                    "nicknames": nicknames,
                    "ratings": ratings,
                    "reports": reports,
                })
            else:
                logger.warning("JobQueue не инициализирован. Периодическое сохранение данных не будет работать. Убедитесь, что установлена зависимость 'python-telegram-bot[job-queue]'.")

            import atexit
            atexit.register(save_data, {
                "search_queue": search_queue,
                "active_chats": active_chats,
                "nicknames": nicknames,
                "ratings": ratings,
                "reports": reports,
            })

            logger.info("Bot is starting...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            break  # Если polling завершился успешно, выходим из цикла

        except Exception as e:
            error_msg = str(e).lower()
            if "getupdates conflict detected" in error_msg or "terminated by other getupdates request" in error_msg or "conflict" in error_msg:
                logger.warning(f"Попытка {attempt + 1}/{max_retries}: Конфликт getUpdates. Ожидание {retry_delay} секунд перед перезапуском...")
                time.sleep(retry_delay)
                if attempt == max_retries - 1:
                    logger.error("Не удалось запустить бот после максимального числа попыток.")
            else:
                logger.error(f"Необработанная ошибка: {e}")
                raise e  # Пробрасываем другие ошибки
        finally:
            # Убедимся, что приложение закрывается корректно
            if 'application' in locals():
                logger.info("Закрытие приложения перед перезапуском...")
                application.stop_running()

    else:
        logger.error(f"Не удалось запустить бот после {max_retries} попыток. Проверьте, не запущены ли другие экземпляры бота.")

if __name__ == "__main__":
    main()