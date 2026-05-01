#!/usr/bin/env python3
"""Event Finder Telegram Bot v9.2.0 — Lean Entry Point."""

import logging
import time
import asyncio
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
from database import EventDatabase
from scheduler import EventScheduler
from notion_service import NotionService
from handlers import (
    start,
    help_command,
    admin_start_scanner,
    admin_stop_scanner,
    admin_status,
    admin_report,
    llm_analysis,
    search_start,
    search_city_selected,
    search_dates_selected,
    search_page,
)

load_dotenv()

db = EventDatabase()
notion_service = NotionService()
scheduler = EventScheduler(db)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)
    try:
        if update and hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.answer("❌ Ошибка, попробуйте снова", show_alert=True)
        elif update and hasattr(update, "message") and update.message:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте /start")
    except Exception:
        pass

_bot_instance = None

def _split_html_chunks(text: str, limit: int = 4000) -> list[str]:
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = current + ("\n" if current else "") + line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

async def on_scheduler_report(report_text: str):
    """Send scheduler report to admin chat."""
    admin_chat_id = await db.get_admin_chat_id()
    if not admin_chat_id:
        logger.warning("No admin chat ID set, cannot send report")
        return

    if not _bot_instance:
        logger.warning("Bot instance not available, cannot send report")
        return

    try:
        for chunk in _split_html_chunks(report_text):
            await _bot_instance.send_message(
                chat_id=admin_chat_id, text=chunk, parse_mode="HTML"
            )
        logger.info(f"Report sent to chat {admin_chat_id}")
    except Exception as e:
        logger.error(f"Error sending report: {e}")

async def on_scheduler_notion(events: list[dict], duration: float) -> str:
    """Automatically add events to Notion Database."""
    if not config.NOTION_API_KEY or not config.NOTION_PARENT_PAGE_ID:
        return ""

    try:
        db_id = await notion_service.ensure_events_database(db)
        if not db_id:
            return ""

        added = await asyncio.to_thread(
            notion_service.add_events_to_database,
            db_id,
            events,
        )

        if not added:
            return ""

        parent_id = config.NOTION_PARENT_PAGE_ID.replace("-", "")
        return f"https://www.notion.so/{parent_id}"

    except Exception as e:
        logger.error(f"Notion auto-create error: {e}")
        return ""

def main():
    logger.info("Event Finder Bot starting...")

    async def post_init(app: Application):
        await db.init_db()
        app.bot_data["db"] = db
        app.bot_data["scheduler"] = scheduler
        app.bot_data["notion_service"] = notion_service
        is_run = await db.is_running()
        if is_run:
            await scheduler.start()

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    application.bot_data["start_time"] = time.time()

    global _bot_instance
    _bot_instance = application.bot

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Menu
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help_cmd$"))

    # Admin Callbacks
    application.add_handler(CallbackQueryHandler(admin_start_scanner, pattern="^admin_start$"))
    application.add_handler(CallbackQueryHandler(admin_stop_scanner, pattern="^admin_stop$"))
    application.add_handler(CallbackQueryHandler(admin_status, pattern="^admin_status$"))
    application.add_handler(CallbackQueryHandler(admin_report, pattern="^admin_report$"))
    application.add_handler(CallbackQueryHandler(llm_analysis, pattern="^llm_analysis$"))

    # Search Callbacks
    application.add_handler(CallbackQueryHandler(search_start, pattern="^search_start$"))
    application.add_handler(CallbackQueryHandler(search_city_selected, pattern="^city_"))
    application.add_handler(CallbackQueryHandler(search_dates_selected, pattern="^period_"))

    # Pagination Callbacks
    application.add_handler(CallbackQueryHandler(search_page, pattern="^page_"))

    # Fallback — redirect to menu
    async def handle_message(update: Update, context):
        await start(update, context)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.add_error_handler(error_handler)

    # Set scheduler report callbacks
    scheduler.set_on_report(on_scheduler_report)
    scheduler.set_on_notion_page(on_scheduler_notion)

    # Start bot
    logger.info("Bot is running. Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
