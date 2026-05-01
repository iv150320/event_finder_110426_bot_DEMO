from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import config


def _db(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["db"]


def _is_admin(user_id: int) -> bool:
    if not config.ALLOWED_USERS:
        return True
    return user_id in config.ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if config.ALLOWED_USERS and user_id not in config.ALLOWED_USERS:
        if update.message:
            await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
        return

    context.user_data.pop("city", None)
    context.user_data.pop("topic", None)
    # Не трогаем search_results / search_page / search_params —
    # пользователь может вернуться в меню во время просмотра результатов

    if not await _db(context).get_admin_chat_id():
        await _db(context).set_admin_chat_id(user_id)

    running = "🟢 Работает" if await _db(context).is_running() else "🔴 Остановлен"

    keyboard = [
        [InlineKeyboardButton("🔍 Найти мероприятия", callback_data="search_start")],
        [InlineKeyboardButton("🧠 Анализ", callback_data="llm_analysis"), InlineKeyboardButton("📋 Отчёт", callback_data="admin_report")],
    ]

    if _is_admin(user_id):
        scanner_btn = (
            InlineKeyboardButton("⏹ Остановить", callback_data="admin_stop")
            if await _db(context).is_running()
            else InlineKeyboardButton("▶️ Запустить сканер", callback_data="admin_start")
        )
        keyboard.append([scanner_btn, InlineKeyboardButton("📊 Статус", callback_data="admin_status")])

    keyboard.append([InlineKeyboardButton("❓ Помощь", callback_data="help_cmd")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📅 <b>Event Finder Bot v9.2.0</b>\n\n"
        f"📊 Сканер: {running}\n"
        f"📚 Источники: 18 вузов + KudaGo + Timepad + Яндекс.Афиша\n"
        f"🔄 Интервал: 30 мин\n\n"
        f"👇 <b>Выберите действие:</b>"
    )
    if update.message:
        await update.message.reply_html(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]
    text = (
        "📖 <b>Справка</b>\n\n"
        "🔍 <b>Найти мероприятия</b> — выберите город и период\n"
        "🧠 <b>Анализ</b> — LLM-дайджест новых событий от DeepSeek\n"
        "📋 <b>Отчёт</b> — последние найденные события\n\n"
        "Бот автоматически сканирует источники каждые 30 мин "
        "и присылает дайджест новых событий каждый час.\n"
    )
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
