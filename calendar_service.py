#!/usr/bin/env python3
"""Calendar service — generates ICS (iCal) feed from database events.

Every event contains:
- DTSTART/DTEND: date range (Europe/Moscow timezone or all-day)
- SUMMARY: event title
- DESCRIPTION: description with source
- URL: link to event registration page
- LOCATION: place/source
- UID: unique identifier

Smart time handling:
- If time is 00:00 → all-day event (VALUE=DATE)
- If time is set → timed event with TZID=Europe/Moscow
"""

import hashlib
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def escape_ical_text(text: str) -> str:
    """Escape text for iCal format."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(",", "\\,")
    text = text.replace(";", "\\;")
    text = text.replace("\n", "\\n")
    return text


def format_ical_date(dt: datetime) -> str:
    """Format as all-day date: YYYYMMDD."""
    return dt.strftime("%Y%m%d")


def format_ical_datetime(dt: datetime) -> str:
    """Format datetime: YYYYMMDDTHHMMSS."""
    return dt.strftime("%Y%m%dT%H%M%S")


def generate_ics(events: list[dict], title: str = "Event Finder") -> str:
    """Generate ICS calendar content from events.

    Smart time handling:
    - Events at 00:00 → all-day (VALUE=DATE) — shows as "весь день" в календаре
    - Events with time → timed with TZID=Europe/Moscow
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Event Finder Bot//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ical_text(title)}",
        "X-WR-TIMEZONE:Europe/Moscow",
        "X-WR-CALDESC:Мероприятия из Event Finder Bot (вузы, компании, KudaGo, Timepad)",
        # Timezone definition for Europe/Moscow
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Moscow",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0300",
        "TZOFFSETTO:+0300",
        "TZNAME:MSK",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for ev in events:
        # Parse date
        dt = ev.get("date_sort")
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except (ValueError, TypeError):
                continue
        if not dt:
            continue

        # Strip timezone
        dt_local = dt.replace(tzinfo=None)

        # Detect if this is an all-day event (time is 00:00)
        is_all_day = dt_local.hour == 0 and dt_local.minute == 0

        title_text = ev.get("title", "Без названия")
        source = ev.get("source", "")
        place = ev.get("place", "")
        url = ev.get("url", "")
        topic = ev.get("topic", "")

        # UID — deterministic from title + date
        uid_str = f"{title_text}-{ev.get('date', '')}-{source}"
        uid = hashlib.md5(uid_str.encode()).hexdigest() + "@eventfinder"

        # Build description
        desc_parts = []
        desc_parts.append(f"Источник: {source}")
        if place:
            desc_parts.append(f"Место: {place}")
        if topic:
            desc_parts.append(f"Тема: {topic}")
        if url:
            desc_parts.append(f"Подробнее: {url}")

        description = escape_ical_text("\n".join(desc_parts))
        summary = escape_ical_text(title_text[:255])
        location = escape_ical_text(place) if place else escape_ical_text(source)

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")

        if is_all_day:
            # All-day event — VALUE=DATE, no time
            end_date = dt_local + timedelta(days=1)
            lines.append(f"DTSTART;VALUE=DATE:{format_ical_date(dt_local)}")
            lines.append(f"DTEND;VALUE=DATE:{format_ical_date(end_date)}")
        else:
            # Timed event with timezone
            end_dt = dt_local + timedelta(hours=2)
            lines.append(f"DTSTART;TZID=Europe/Moscow:{format_ical_datetime(dt_local)}")
            lines.append(f"DTEND;TZID=Europe/Moscow:{format_ical_datetime(end_dt)}")

        lines.append(f"SUMMARY:{summary}")
        lines.append(f"DESCRIPTION:{description}")
        lines.append(f"LOCATION:{location}")

        if url:
            lines.append(f"URL:{url}")

        dtstamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"CATEGORIES:{escape_ical_text(source)},{escape_ical_text(topic)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
