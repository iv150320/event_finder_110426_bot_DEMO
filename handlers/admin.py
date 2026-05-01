from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import html
from nvidia_service import generate_hourly_llm_report


def _db(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["db"]


def _scheduler(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["scheduler"]

async def admin_start_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await _db(context).is_running():
        await query.edit_message_text("🟢 Сканер уже работает.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]))
        return

    await query.edit_message_text("🔄 Запускаю сканер событий...")
    await _scheduler(context).start()
    await query.edit_message_text(
        "✅ <b>Сканер запущен!</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
    )

async def admin_stop_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _db(context).is_running():
        await query.edit_message_text("🔴 Сканер уже остановлен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]))
        return

    await query.edit_message_text("⏳ Останавливаю сканер...")
    await _scheduler(context).stop()
    await query.edit_message_text(
        f"⏹ <b>Сканер остановлен</b>\n\n📊 Всего в базе: {await _db(context).get_total_count()}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
    )

async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    status_dict = await _db(context).get_status()
    is_running = status_dict.get("is_running", False)
    
    text = (
        f"📊 <b>Статус сканера:</b>\n"
        f"Состояние: {'🟢 Работает' if is_running else '🔴 Остановлен'}\n"
        f"Интервал: {status_dict.get('scan_interval_minutes', 30)} мин\n"
        f"Всего событий: {status_dict.get('total_events', 0)}\n"
        f"Новых событий: {status_dict.get('new_events', 0)}"
    )
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]))

async def admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_events = await _db(context).get_new_events(limit=30)
    if not new_events:
        await query.edit_message_text(
            "📋 Нет новых событий.\n\nЗапустите сканер.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
        return

    lines = [f"📋 <b>Отчёт: {len(new_events)} новых событий</b>\n"]
    for ev in new_events[:10]:
        title = html.escape(ev.get("title", "")[:120])
        url = html.escape(ev.get("url", ""), quote=True)
        if url:
            lines.append(f'• <a href="{url}">{title}</a>')
        else:
            lines.append(f'• {title}')

    if len(new_events) > 10:
        lines.append(f"... и ещё {len(new_events) - 10}")

    shown_event_ids = [ev["id"] for ev in new_events if ev.get("id") is not None]
    if shown_event_ids:
        await _db(context).mark_events_reported(shown_event_ids)

    result = "\n".join(lines)
    await query.edit_message_text(result, parse_mode="HTML", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]))

async def llm_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает LLM-анализ новых событий через DeepSeek."""
    query = update.callback_query
    await query.answer()

    unreported = await _db(context).get_unreported_llm_events(limit=500)
    if not unreported:
        await query.edit_message_text(
            "🧠 <b>LLM-анализ</b>\n\nНет новых событий для анализа.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
        return

    await query.edit_message_text(f"🧠 <b>Анализирую {len(unreported)} событий через DeepSeek...</b>", parse_mode="HTML")

    events_text = "\n".join(
        f"- {e.get('title', 'Без названия')} ({e.get('date', '')}) [Источник: {e.get('source', '')}]"
        for e in unreported
    )

    report_text = await generate_hourly_llm_report(events_text)

    if not report_text or "Нет новых релевантных событий" in report_text:
        await query.edit_message_text(
            "🧠 <b>LLM-анализ</b>\n\nНет релевантных событий по выбранным темам.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
        return

    event_ids = [e["id"] for e in unreported if e.get("id") is not None]
    if event_ids:
        await _db(context).mark_events_llm_reported(event_ids)

    header = "🧠 <b>Анализ от DeepSeek</b>\n\n"
    full_text = header + report_text

    if len(full_text) > 4000:
        chunks = []
        lines = full_text.split("\n")
        current = ""
        for line in lines:
            candidate = current + ("\n" if current else "") + line
            if len(candidate) > 3800 and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks):
            if i == 0:
                await query.edit_message_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
            else:
                await query.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
        await query.message.reply_text(
            "👇",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
    else:
        await query.edit_message_text(
            full_text, parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
