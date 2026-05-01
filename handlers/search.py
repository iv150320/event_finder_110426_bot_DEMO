from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from event_search import CITY_MAP, UNIVERSITIES, fetch_kudago, fetch_timepad, fetch_university, dedupe, process_events_pipeline
import config
from datetime import datetime, timedelta
import asyncio
import html
import logging

ITEMS_PER_PAGE = 5
SEARCH_TIMEOUT = 30

_logger = logging.getLogger(__name__)

def _db(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["db"]

_KUDAGO_SOURCES = {"KudaGo", "Timepad", "Яндекс.Афиша", "Rusbase", "Tadviser",
    "Eventbrite", "Сбер", "VK"}
_MOSCOW_UNI_SOURCES = {"ВШЭ", "МГУ Экономфак", "МГУ ВМК", "МГУ Юрфак",
    "РЭУ им. Плеханова", "РЭУ им. Плешанова", "РАНХиГС", "МГТУ Баумана",
    "МИФИ", "РУДН", "Финансовый университет", "МАИ",
    "МГУ Журфак"}


def _city_sources(city: str) -> list[str]:
    if city.lower().strip() == "москва":
        return list(_KUDAGO_SOURCES | _MOSCOW_UNI_SOURCES)
    return list(_KUDAGO_SOURCES)


def _get_dates_for_period(period: str):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return today, today.replace(hour=23, minute=59, second=59), "Сегодня"
    elif period == "week":
        days_until_sunday = (6 - today.weekday()) % 7
        end = today + timedelta(days=days_until_sunday, hours=23, minutes=59, seconds=59)
        return today, end, "Текущая неделя"
    elif period == "nextweek":
        next_monday = today + timedelta(days=(7 - today.weekday()))
        return next_monday, next_monday + timedelta(days=6, hours=23, minutes=59, seconds=59), "Следующая неделя"
    elif period == "month":
        import calendar
        _, last_day = calendar.monthrange(today.year, today.month)
        return today, today.replace(day=last_day, hour=23, minute=59, second=59), "Весь месяц"
    return today, today + timedelta(days=30, hours=23, minutes=59, seconds=59), "Весь месяц"


async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("Москва", callback_data="city_Москва"), InlineKeyboardButton("СПб", callback_data="city_СПб")],
        [InlineKeyboardButton("Екатеринбург", callback_data="city_Екатеринбург"), InlineKeyboardButton("Новосибирск", callback_data="city_Новосибирск")],
        [InlineKeyboardButton("Казань", callback_data="city_Казань"), InlineKeyboardButton("Сочи", callback_data="city_Сочи")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]
    ]
    await query.edit_message_text("🏙 <b>Выберите город:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def search_city_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    city = query.data.split("_")[1]
    context.user_data["city"] = city

    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="period_today")],
        [InlineKeyboardButton("📅 Текущая неделя", callback_data="period_week")],
        [InlineKeyboardButton("📅 Следующая неделя", callback_data="period_nextweek")],
        [InlineKeyboardButton("📅 Весь месяц", callback_data="period_month")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="search_start"), InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]
    ]
    await query.edit_message_text(f"🏙 Город: <b>{city}</b>\n\n📆 <b>Выберите период:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def _fetch_events_list(db, city: str, start: datetime, end: datetime):
    city_raw = city.lower().strip()
    city_slug = CITY_MAP.get(city_raw, "msk")
    all_events = []

    # Сначала — из БД (быстро, всегда есть данные)
    try:
        all_sources = await db.get_all_sources()
        db_events = await db.search_events(all_sources, start, end, limit=200)
        for e in db_events:
            ds = e.get("date_sort")
            if isinstance(ds, str):
                try:
                    e["date_sort"] = datetime.fromisoformat(ds)
                except ValueError:
                    e["date_sort"] = None
        all_events.extend(db_events)
    except Exception as exc:
        _logger.warning("DB search failed: %s", exc)

    # Потом — живой поиск по API (обогащение)
    try:
        k = await fetch_kudago(city_slug, "", start, end)
        all_events.extend(k)
    except Exception as exc:
        _logger.warning("KudaGo fetch failed: %s", exc)

    try:
        t = await fetch_timepad(city_slug, "", start, end)
        all_events.extend(t)
    except Exception as exc:
        _logger.warning("Timepad fetch failed: %s", exc)

    if city_raw == "москва":
        async def _fetch_one(uni):
            try:
                return await fetch_university(uni["url"], uni["name"], start, end)
            except Exception:
                return []
        tasks = [_fetch_one(u) for u in UNIVERSITIES[:5]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_events.extend(r)

    all_events = dedupe(all_events)
    all_events = process_events_pipeline(all_events, config.SCHEDULER_ALLOWED_TOPICS)
    all_events.sort(key=lambda e: e.get("date_sort") or datetime.max)
    return all_events


async def search_dates_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period = query.data.split("_")[1]

    city = context.user_data.get("city", "Москва")

    start, end, period_label = _get_dates_for_period(period)

    await query.edit_message_text(
        f"🔍 <b>Ищу:</b>\n🏙 Город: {city}\n📆 Период: {period_label}\n\n⏳ Это может занять несколько секунд...",
        parse_mode="HTML"
    )

    try:
        events = await asyncio.wait_for(
            _fetch_events_list(_db(context), city, start, end),
            timeout=SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _logger.warning("Search timed out for %s / %s", city, period_label)
        events = []
    except Exception as e:
        _logger.error("Search error: %s", e)
        events = []

    context.user_data["search_results"] = events
    context.user_data["search_page"] = 0
    context.user_data["search_params"] = f"{city}, {period_label}"

    await render_search_page(query, context)


async def render_search_page(query, context):
    events = context.user_data.get("search_results", [])
    page = context.user_data.get("search_page", 0)
    params = context.user_data.get("search_params", "")

    if not events:
        await query.edit_message_text(
            f"📅 <b>{params}</b>\n\nНичего не найдено.", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]])
        )
        return

    total_pages = max(1, (len(events) - 1) // ITEMS_PER_PAGE + 1)
    page = max(0, min(page, total_pages - 1))
    context.user_data["search_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_events = events[start_idx:end_idx]

    lines = [f"📅 <b>{params}</b>", f"📊 Всего: {len(events)} (Стр {page+1}/{total_pages})\n"]
    for ev in page_events:
        title = html.escape(ev.get("title", ""))
        url = ev.get("url", "")
        date_str = ev.get("date", "")
        source = ev.get("source", "")
        if url:
            lines.append(f'▪️ <a href="{url}">{title}</a>\n  📆 {date_str}  📍 {source}')
        else:
            lines.append(f'▪️ {title}\n  📆 {date_str}  📍 {source}')
        lines.append("")

    text = "\n".join(lines)

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Пред", callback_data="page_prev"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("След ➡️", callback_data="page_next"))

    keyboard = []
    if buttons:
        keyboard.append(buttons)
    keyboard.append([InlineKeyboardButton("🔍 Новый поиск", callback_data="search_start"), InlineKeyboardButton("🏠 Меню", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))


async def search_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
    page = context.user_data.get("search_page", 0)

    if action == "page_prev":
        context.user_data["search_page"] = page - 1
    elif action == "page_next":
        context.user_data["search_page"] = page + 1

    await render_search_page(query, context)
