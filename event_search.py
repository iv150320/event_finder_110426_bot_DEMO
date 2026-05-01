#!/usr/bin/env python3
"""Event Finder v9.2.0 — расширенный поиск мероприятий с улучшенным парсингом.

Источники:
1. KudaGo public API — с пагинацией (все страницы)
2. Timepad API — расширенные параметры
3. Яндекс.Афиша — HTML парсинг
4. Университеты — многоуровневая стратегия парсинга
5. Eventbrite — API

Улучшения:
- Retry с exponential backoff
- Ротация User-Agent
- Пагинация для API с лимитами
- Улучшенная классификация по всем полям
- Асинхронные запросы

Кэширует результаты на 30 минут.
Дедуплицирует по normalize(title) + date.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# New imports for improved architecture
from cache_manager import cache_manager
from error_handling import with_retry, async_with_retry

logger = logging.getLogger(__name__)

# ─── Retry Configuration ─────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1  # секунды
RETRY_DELAY_MAX = 10

# ─── User-Agent Rotation ───────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

# ─── Constants ───────────────────────────────────────────────────────────────

SKILL_DIR = Path(__file__).parent
CACHE_FILE = SKILL_DIR / "data" / "cache.db"  # For backward compatibility
CACHE_TTL = 1800 # 30 минут

VERSION = "8.0.1"

# ─── City mapping ────────────────────────────────────────────────────────────

CITY_MAP = {
    "москва": "msk",
    "санкт-петербург": "spb",
    "спб": "spb",
    "питер": "spb",
    "екатеринбург": "ekb",
    "новосибирск": "nsk",
    "нижний новгород": "nnv",
    "казань": "kzn",
    "самара": "smr",
    "краснодар": "krd",
    "сочи": "sochi",
    "уфа": "ufa",
    "красноярск": "krasnoyarsk",
}

# ─── University sources ─────────────────────────────────────────────────────
# Only sites that render content server-side (no JS required)

UNIVERSITIES = [
    {"name": "ВШЭ", "url": "https://www.hse.ru/announcements"},
    {"name": "МГУ Экономфак", "url": "https://www.econ.msu.ru/events/"},
    {"name": "МГУ ВМК", "url": "https://cs.msu.ru/news/events"},
    {"name": "МГУ Юрфак", "url": "https://www.law.msu.ru/calendar"},
    {"name": "РЭУ им. Плеханова", "url": "https://www.rea.ru/events/"},
    {"name": "РАНХиГС", "url": "https://www.ranepa.ru/news/"},
    {"name": "МГТУ Баумана", "url": "https://bmstu.ru/news"},
    {"name": "МИФИ", "url": "https://mephi.ru"},
    {"name": "РУДН", "url": "https://www.rudn.ru"},
    {"name": "Финансовый университет", "url": "https://www.fa.ru"},
    {"name": "МАИ", "url": "https://mai.ru"},
]

# ─── Additional Event Sources ────────────────────────────────────────────────

ADDITIONAL_SOURCES = [
    {"name": "Яндекс.Афиша", "type": "yandex_afisha"},
    {
        "name": "Eventbrite",
        "url": "https://www.eventbrite.com/d/russia--moscow/events/",
        "type": "html",
    },
]

# ─── Corporate & business event sources ─────────────────────────────────────

CORPORATE_SOURCES = [
    {"name": "Сбер", "url": "https://www.sberbank.com/events"},
    {"name": "VK", "url": "https://vk.company/ru/press/events/"},
]

# ─── Tech & Business Event Sources ───────────────────────────────────────────

TECH_BUSINESS_SOURCES = [
    {"name": "Rusbase", "url": "https://rb.ru/events/", "follow_redirects": True},
    {
        "name": "Tadviser",
        "url": "https://www.tadviser.ru/index.php/%D0%9A%D0%B0%D1%82%D0%B5%D0%B3%D0%BE%D1%80%D0%B8%D1%8F:%D0%A1%D0%BE%D0%B1%D1%8B%D1%82%D0%B8%D1%8F",
    },
]

# ─── Timepad API ────────────────────────────────────────────────────────────

TIMEPAD_CITIES = {
    "москва": "1",
    "санкт-петербург": "2",
    "спб": "2",
    "питер": "2",
    "екатеринбург": "10",
    "новосибирск": "13",
    "казань": "15",
    "нижний новгород": "17",
    "самара": "23",
    "краснодар": "24",
    "сочи": "25",
    "уфа": "26",
    "красноярск": "27",
}

# ─── Session Factory ──────────────────────────────────────────────────────

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}


def _make_client(verify: bool = True) -> httpx.AsyncClient:
    """Create a fresh requests.Session with rotated User-Agent."""
    s = httpx.AsyncClient(timeout=15.0, verify=verify)
    s.headers.update(_DEFAULT_HEADERS)
    s.headers["User-Agent"] = random.choice(USER_AGENTS)
    return s

# ─── Retry Helper ─────────────────────────────────────────────────────────────


def _rotate_user_agent(session: httpx.AsyncClient):
    """Rotate User-Agent for the given session."""
    session.headers["User-Agent"] = random.choice(USER_AGENTS)


@async_with_retry(max_retries=MAX_RETRIES, delay=RETRY_DELAY_BASE,
                   exceptions=(httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError))
async def _fetch_with_retry(url: str, method="GET", verify: bool = True, **kwargs) -> Optional[httpx.Response]:
    """Fetch URL with exponential backoff retry.

    Retries on: 5xx, 429, timeout, connection errors.
    Returns None on: 4xx (non-429) client errors.
    Raises on: unexpected errors (programming bugs, etc).
    """
    async with _make_client(verify=verify) as session:
        _rotate_user_agent(session)
        resp = await session.request(method, url, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                logger.warning(f"Client error {e.response.status_code} on {url}")
                return None
            raise
        return resp


# ─── Advanced Topic Classification ─────────────────────────────────────────

TOPIC_ALIASES = {
    "бизнес": {"бизнес", "business"},
    "it": {"it", "айти", "technology", "технологии", "tech"},
    "ai": {"ai", "artificial intelligence", "искусственный интеллект"},
    "экономика": {"экономика", "economics", "эконом", "финансы"},
    "политика": {"политика", "politics", "polit"},
    "история": {"история", "history"},
    "английский язык": {"английский", "english", "англ", "ielts"},
    "психология": {"психология", "psychology"},
    "литература": {"литература", "literature", "книги"},
    "концерты": {"концерты", "concert", "concerts"},
    "конференции": {"конференции", "conference", "conferences", "конференция"},
    "лекции": {"лекции", "lecture", "lectures", "talk", "talks"},
}


def _topic_variants(topic: str) -> set[str]:
    topic_lower = (topic or "").strip().lower()
    if not topic_lower:
        return set()
    variants = set(TOPIC_ALIASES.get(topic_lower, set()))
    variants.add(topic_lower)
    return {item for item in variants if item}


def matches_topic(event: dict, topic: str) -> bool:
    """Return True when event matches the requested topic."""
    variants = _topic_variants(topic)
    if not variants:
        return True

    detected_topic = classify_topic_advanced(event)
    if detected_topic and detected_topic in variants:
        return True

    searchable_parts = [
        event.get("title", ""),
        event.get("description", ""),
        event.get("place", ""),
        event.get("source", ""),
        " ".join(str(item) for item in event.get("categories", []) or []),
        event.get("topic", ""),
    ]
    searchable = " ".join(part.lower() for part in searchable_parts if part)
    return any(variant in searchable for variant in variants)


_KUDAGO_EXCLUDED_CATEGORIES = {
    "concert", "entertainment", "theater", "party", "stock",
    "tour", "cinema", "kids", "festival", "fashion", "photo",
    "recreation", "yarmarki-razvlecheniya-yarmarki",
}
_KUDAGO_NEUTRAL_CATEGORIES = {"education", "other", "exhibition"}

TOPIC_KEYWORDS = {
    "бизнес": {
        "keywords": [
            "бизнес", "business", "стартап", "startup", "предприним",
            "коммерч", "компани", "корпорац", "ceo", "founder",
            "инвестор", "венчур", "vc", "angel", "pitch",
            "менеджм", "управлен", "руководит", "бизнес-завтрак",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "IT": {
        "keywords": [
            "технолог", "tech", "digital", "цифров", "инноваци",
            "разработ", "программир", "software", "hardware",
            "нейросет", "python", "javascript", "мобильн", "cloud",
            "devops", "backend", "frontend", "фронтенд", "бэкенд",
            "кибербез", "cyber", "платформ", "инженер", "сёрвис",
            "web", "app", "it", "айти",
        ],
        "weight": 1.0,
        "word_boundary": True,
    },
    "AI": {
        "keywords": [
            "ai", "ml", "machine learning", "data science",
            "big data", "artificial intelligence", "gpt", "llm",
            "chatgpt", "deep learning", "искусственн", "интеллект",
            "data mining", "nlp", "computer vision",
        ],
        "weight": 1.0,
        "word_boundary": True,
    },
    "экономика": {
        "keywords": [
            "эконом", "economic", "финанс", "finance", "инвестиц",
            "банк", "рынок", "рынк", "криптовал", "криптом",
            "блокчейн", "blockchain", "трейдинг", "forex", "налог",
            "ввп", "инфляц", "макроэконом", "микроэконом",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "политика": {
        "keywords": [
            "политик", "polit", "государств", "правительств",
            "парламент", "демократ", "выбор", "голосован",
            "законодат", "конституц", "реформ", "дипломат",
            "международн", "геополитик", "оппозиц",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "история": {
        "keywords": [
            "истори", "histor", "археолог", "культур", "наслед",
            "музей", "выставк истор", "ретро", "винтаж", "heritage",
            "цивилизац", "древн", "средневеков", "революц",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "английский язык": {
        "keywords": [
            "английск", "english", "ielts", "toefl", "esl",
            "языков", "language school", "инглиш", "cambridge",
            "англ", "перевод", "билингв", "филолог",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "психология": {
        "keywords": [
            "психолог", "psycholog", "ментальн", "когнитив",
            "поведен", "эмоци", "мотивац", "тренинг",
            "soft skills", "личностный рост", "саморазвитие",
            "mindfulness", "коучинг", "терапи", "осознан",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
    "литература": {
        "keywords": [
            "литератур", "literatur", "книг", "book", "писател",
            "author", "роман", "novel", "поэз", "poetry",
            "проз", "стих", "чтен", "reading", "библиотек",
            "library", "издател", "publish",
        ],
        "weight": 1.0,
        "word_boundary": False,
    },
}


def classify_topic_advanced(event: dict) -> str:
    """Classify event topic using title, description, categories, and place.

    Returns: topic name or empty string
    """
    cats = event.get("categories", [])
    if isinstance(cats, list):
        cat_set = set(str(c).lower() for c in cats)
        has_excluded = bool(cat_set & _KUDAGO_EXCLUDED_CATEGORIES)
        if has_excluded:
            non_neutral = cat_set - _KUDAGO_NEUTRAL_CATEGORIES
            if non_neutral:
                return ""

    searchable_parts = []

    title = event.get("title", "")
    if title:
        searchable_parts.append((title.lower(), 3))

    desc = event.get("description", "")
    if desc:
        searchable_parts.append((desc.lower(), 2))

    if isinstance(cats, list):
        searchable_parts.append((" ".join(str(c) for c in cats).lower(), 2))

    place = event.get("place", "")
    if place:
        searchable_parts.append((place.lower(), 1))

    scores = {}
    for topic, cfg in TOPIC_KEYWORDS.items():
        score = 0
        use_boundary = cfg.get("word_boundary", False)
        for text, weight in searchable_parts:
            for kw in cfg["keywords"]:
                if use_boundary and len(kw) <= 5 and kw.isascii():
                    count = len(re.findall(rf'\b{re.escape(kw)}\b', text))
                else:
                    count = text.count(kw)
                if count > 0:
                    score += count * weight * cfg["weight"]
        if score > 0:
            scores[topic] = score

    if scores:
        best_topic = max(scores, key=scores.get)
        if scores[best_topic] >= 3:
            return best_topic

    return ""


# ─── Month helpers ───────────────────────────────────────────────────────────

MONTH_NAMES = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
DAY_OF_WEEK = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]
MONTH_MAP_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}


def ru_date(dt: datetime) -> str:
    """9 апреля, вторник"""
    return f"{dt.day} {MONTH_NAMES[dt.month]}, {DAY_OF_WEEK[dt.weekday()]}"


def ru_date_range(start: datetime, end: datetime) -> str:
    """9–10 апреля"""
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTH_NAMES[start.month]}"
    return (
        f"{start.day} {MONTH_NAMES[start.month]} – {end.day} {MONTH_NAMES[end.month]}"
    )


def parse_date_mixed(text: str) -> Optional[datetime]:
    """Извлекает первую найденную дату из текста."""
    if not text:
        return None

    # Epoch timestamp
    m = re.search(r"\b(\d{10})\b", text)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)))
        except (OSError, ValueError):
            pass

    # DD.MM.YYYY
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return datetime(y, mo, d)

    # YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return datetime(y, mo, d)

    # "9 апреля" / "9 апреля 2026" / "April 9, 2026"
    m = re.search(r"(\d{1,2})\s+([а-яёa-zA-Z]+)\s*(\d{4})?", text)
    if m:
        day = int(m.group(1))
        month_str = m.group(2).lower()
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        month = MONTH_MAP_RU.get(month_str)
        if month and 1 <= day <= 31:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

    return None


async def fetch_habr(topic: str, start: datetime, end: datetime, max_events: int = 50) -> list[dict]:
    # (Implementation for habr)
    events = []
    url = "https://habr.com/ru/events/"
    resp = await _fetch_with_retry(url, timeout=15)
    if not resp:
        return events
    try:
        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all(class_='tm-event-card')
        for card in cards:
            title_tag = card.find(class_='tm-title__link')
            if not title_tag: continue
            title = title_tag.get_text(strip=True)
            event_url = urljoin("https://habr.com", title_tag.get('href', ''))
            date_tag = card.find(class_='tm-event-date__text')
            date_text = date_tag.get_text(strip=True) if date_tag else ""
            dt = parse_date_mixed(date_text)
            if not dt or dt < start or dt > end + timedelta(days=7): continue
            place = "Онлайн" if "онлайн" in card.get_text(strip=True).lower() else "Офлайн"
            events.append({"title": title[:200], "date": ru_date(dt), "date_sort": dt, "place": place, "url": event_url, "source": "Habr Events"})
            if len(events) >= max_events: break
    except Exception as e:
        logger.warning(f"Habr parsing error: {e}")
    return events

# ─── Cache ───────────────────────────────────────────────────────────────────


def _cache_key(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def process_events_pipeline(raw_events: List[Dict], allowed_topics: List[str] = None) -> List[Dict]:
    """
    New event processing pipeline with correct order:
    1. Deduplicate events
    2. Classify topics
    3. Filter by allowed topics
    """
    if not raw_events:
        return []

    # 1. Deduplicate first
    unique_events = dedupe(raw_events)

    # 2. Classify topics for unique events
    classified_events = []
    for event in unique_events:
        event_copy = dict(event)
        if not event_copy.get("topic"):
            event_copy["topic"] = classify_topic_advanced(event_copy)
        classified_events.append(event_copy)

    # 3. Filter by allowed topics if specified
    if allowed_topics:
        filtered_events = [
            event for event in classified_events
            if event.get("topic") in allowed_topics
        ]
        return filtered_events

    return classified_events


def cache_get(key: str):
    """Get cached data using new cache manager."""
    return cache_manager.get(key)


def cache_set(key: str, data):
    """Set cached data using new cache manager."""
    cache_manager.set(key, data)


def _serialize_events(events: list[dict]) -> list[dict]:
    """Convert datetime objects to ISO strings for JSON cache."""
    result = []
    for e in events:
        entry = dict(e)
        if isinstance(entry.get("date_sort"), datetime):
            entry["date_sort"] = entry["date_sort"].isoformat()
        result.append(entry)
    return result


def _deserialize_events(events: list[dict]) -> list[dict]:
    """Restore datetime objects from ISO strings."""
    result = []
    for e in events:
        entry = dict(e)
        ds = entry.get("date_sort")
        if isinstance(ds, str):
            try:
                entry["date_sort"] = datetime.fromisoformat(ds)
            except ValueError:
                entry["date_sort"] = None
        result.append(entry)
    return result


# ─── Fetchers ────────────────────────────────────────────────────────────────


def strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(clean)).strip()


def _is_valid_url(url: str) -> bool:
    """Basic URL validation — must start with http/https and have a netloc."""
    if not url:
        return False
    return bool(re.match(r"^https?://\S+\.\S+", url))


async def fetch_kudago(
    city_slug: str, topic: str, start: datetime, end: datetime, max_events: int = 300
) -> list[dict]:
    """KudaGo public API — с пагинацией для получения всех событий.

    Args:
        city_slug: Slug города (msk, spb и т.д.)
        topic: Тема для фильтрации
        start: Начальная дата
        end: Конечная дата
        max_events: Максимальное количество событий (по умолчанию 300)
    """
    ck = _cache_key("kudago", city_slug, topic, start.strftime("%Y%m%d"))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    url = "https://kudago.com/public-api/v1.4/events/"
    events: list[dict] = []
    page = 1
    max_pages = 5  # Limit to avoid too many requests

    while page <= max_pages and len(events) < max_events:
        params = {
            "location": city_slug,
            "page_size": 100,
            "page": page,
            "order_by": "dates__start",
            "fields": "id,title,description,site_url,place,dates,categories,is_free",
            "actual_since": int(start.timestamp()),  # API expects timestamp
            "actual_until": int((end + timedelta(days=30)).timestamp()),
        }

        resp = await _fetch_with_retry(url, params=params, timeout=20)
        if not resp:
            break

        try:
            data = resp.json()
        except json.JSONDecodeError:
            break

        results = data.get("results", [])
        if not results:
            break

        for item in results:
            dates = item.get("dates", [])
            if not dates:
                continue

            # Найти ближайшую дату в запрошенном диапазоне
            # KudaGo для ongoing-событий ставит start=-62135433000 (год 0001),
            # реальная дата только в end — учитываем это
            _KUDAGO_NULL_TS = -62135520000  # ~ 0001-01-01
            nearest_dt = None
            for d in dates:
                ts_start = d.get("start", 0) or 0
                ts_end = d.get("end", 0) or 0
                if ts_start > _KUDAGO_NULL_TS and ts_start > 0:
                    dt = datetime.fromtimestamp(ts_start)
                elif ts_end > 0:
                    dt = datetime.fromtimestamp(ts_end)
                else:
                    continue
                if start <= dt <= end + timedelta(days=7):
                    if nearest_dt is None or dt < nearest_dt:
                        nearest_dt = dt

            # Если нет дат в диапазоне — пропускаем
            if nearest_dt is None:
                continue

            event_dt = nearest_dt

            title = strip_html(item.get("title", ""))
            if not title or len(title) < 5:
                continue

            # Get full description for better classification
            desc = strip_html(item.get("description", "") or "")
            cats_raw = item.get("categories", [])
            cats = []
            for c in cats_raw:
                if isinstance(c, dict):
                    cats.append(c.get("name", "").lower())
                elif isinstance(c, str):
                    cats.append(c.lower())

            place = ""
            if item.get("place"):
                place = strip_html(item.get("place", {}).get("name", ""))

            if topic:
                event_data = {
                    "title": title,
                    "description": desc,
                    "categories": cats,
                    "place": place,
                    "source": "KudaGo",
                }
                if not matches_topic(event_data, topic):
                    continue

            # Форматируем дату — показываем ближайшую дату в диапазоне
            try:
                date_str = ru_date(event_dt) if event_dt else ""
            except Exception:
                date_str = ""

            # Build event dict with classification
            site_url = item.get("site_url", "")
            event = {
                "title": title,
                "date": date_str,
                "date_sort": event_dt,
                "place": place,
                "url": site_url if _is_valid_url(site_url) else "",
                "source": "KudaGo",
                    "is_free": item.get("is_free", False),
                    "description": desc[:500] if desc else "",
                    "categories": cats,
            }
            # Auto-classify if no topic specified
            event["topic"] = classify_topic_advanced(event) if not topic else ""
            events.append(event)

        if len(events) >= max_events:
                break

        # Check if there are more pages
        if not data.get("next"):
            break

        page += 1
        await asyncio.sleep(0.3)  # Be polite to API

    cache_set(ck, events)
    logger.info(f"KudaGo: fetched {len(events)} events from {page} page(s)")
    return events


async def fetch_timepad(
    city_slug: str, topic: str, start: datetime, end: datetime, max_events: int = 200
) -> list[dict]:
    """Timepad API v1 — с использованием API ключа.

    Документация: https://api.timepad.ru/v1/docs/
    Обязательный параметр: starts_at_min
    """
    import config

    city_id = TIMEPAD_CITIES.get(city_slug)
    if not city_id:
        return []

    ck = _cache_key("timepad_api", city_id, start.strftime("%Y%m%d"))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    events: list[dict] = []
    skip = 0
    limit = 20  # As per API example
    max_pages = 10

    url = "https://api.timepad.ru/v1/events"
    headers = {}

    # Add API key if available
    if config.TIMEPAD_API_KEY:
        headers["Authorization"] = f"Bearer {config.TIMEPAD_API_KEY}"

    page = 0
    while page < max_pages and len(events) < max_events:
        params = {
            "cityId": city_id,
            "limit": limit,
            "skip": skip,
            "access_statuses": "public",  # Only public events
            "starts_at_min": start.strftime("%Y-%m-%d"),  # REQUIRED parameter
        }

        # Optional: ends_at_max
        if end:
            params["starts_at_max"] = (end + timedelta(days=30)).strftime("%Y-%m-%d")

        resp = await _fetch_with_retry(url, params=params, headers=headers, timeout=20)
        if not resp:
            break

        try:
            data = resp.json()
        except json.JSONDecodeError:
            break

        items = data.get("events", [])
        if not items:
            break

        for item in items:
            event_start = item.get("startsAt")
            if not event_start:
                continue

            # Parse ISO 8601 datetime
            try:
                event_dt = datetime.fromisoformat(
                    event_start.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                continue

            # Filter by date range
            if event_dt < start or event_dt > end + timedelta(days=7):
                continue

            title = item.get("name", "").strip() or item.get("title", "").strip()
            if not title or len(title) < 5:
                continue

            # Get description
            desc = (
                item.get("description_short", "") or item.get("description", "") or ""
            )
            desc = strip_html(desc)

            # Get categories
            cats = item.get("categories", [])
            cat_names = [c.get("name", "").lower() for c in cats if isinstance(c, dict)]

            # Get place/location
            place = ""
            location = item.get("location", {})
            if location:
                place = location.get("address", "") or location.get("name", "")

            # Organization as fallback
            if not place:
                org = item.get("organization", {})
                if org:
                    place = org.get("name", "")

            # Build event URL
            event_id = item.get("id", "")
            event_url = (
                f"https://timepad.ru/event/{event_id}/"
                if event_id
                else item.get("url", "")
            )

            # Check if free
            ticket_types = item.get("ticket_types", [])
            is_free = False
            if ticket_types:
                is_free = all(
                    t.get("price", {}).get("current", 0) == 0 for t in ticket_types
                )

            # Build event dict
            try:
                date_display = ru_date(event_dt)
            except Exception:
                date_display = ""

            event = {
                "title": title[:200],
                "date": date_display,
                "date_sort": event_dt,
                "place": place or "Москва",
                "url": event_url if _is_valid_url(event_url) else "",
                "source": "Timepad",
                "is_free": is_free,
                "description": desc[:300] if desc else "",
                "categories": cat_names,
            }

            if topic and not matches_topic(event, topic):
                continue

            # Auto-classify
            event["topic"] = classify_topic_advanced(event) if not topic else ""
            events.append(event)

        if len(events) >= max_events:
            break

        if len(items) < limit:
            break

        skip += limit
        page += 1
        await asyncio.sleep(0.3)

    cache_set(ck, events)
    logger.info(f"Timepad API: fetched {len(events)} events from {page + 1} page(s)")
    return events


async def fetch_yandex_afisha(
    topic: str, start: datetime, end: datetime, max_events: int = 100
) -> list[dict]:
    """Яндекс.Афиша — HTML парсинг.

    Returns events from Yandex Afisha for Moscow.
    """
    # Yandex Afisha uses different URLs based on category
    category_map = {
        "концерты": "concert",
        "выставки": "exhibition",
        "спектакли": "theatre",
        "лекции": "talks",
        "экскурсии": "excursions",
        "кино": "cinema",
        "развлечения": "entertainment",
    }

    ck = _cache_key("yandex_afisha", topic, start.strftime("%Y%m%d"))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    events: list[dict] = []
    base_url = "https://afisha.yandex.ru"

    # Determine category from topic
    topic_lower = topic.lower().strip() if topic else ""
    category = category_map.get(topic_lower, "")

    # Try multiple endpoints
    urls_to_try = []
    if category:
        urls_to_try.append(f"{base_url}/moscow/{category}")
    urls_to_try.append(f"{base_url}/moscow")  # All events

    for url in urls_to_try:
        if len(events) >= max_events:
            break

        resp = await _fetch_with_retry(url, timeout=20)
        if not resp:
            continue

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            continue

        seen_titles = set()

        # Try multiple selectors for event cards
        selectors = [
            "[data-testid='event-card']",
            ".event-card",
            ".card-event",
            "[class*='event']",
            "article",
            ".event-list__item",
        ]

        event_cards = []
        for selector in selectors:
            try:
                if selector.startswith("["):
                    event_cards = soup.select(selector)
                else:
                    event_cards = soup.find_all(
                        class_=re.compile(selector.replace(".", ""), re.I)
                    )
                if event_cards:
                    break
            except Exception:
                continue

        for card in event_cards:
            try:
                title_elem = (
                    card.select_one("[data-testid='event-title']")
                    or card.select_one("h3")
                    or card.select_one("h2")
                    or card.select_one("a")
                    or card.find(class_=re.compile("title|name", re.I))
                )
                title = title_elem.get_text(strip=True) if title_elem else ""

                if not title or len(title) < 5:
                    continue

                title_norm = title.lower()[:100]
                if title_norm in seen_titles:
                    continue
                seen_titles.add(title_norm)

                date_elem = (
                    card.select_one("[data-testid='event-date']")
                    or card.select_one("time")
                    or card.find(class_=re.compile("date|time", re.I))
                )
                date_text = date_elem.get_text(strip=True) if date_elem else ""
                dt = parse_date_mixed(date_text) or parse_date_mixed(title)

                if not dt:
                    continue

                if dt < start or dt > end + timedelta(days=7):
                    continue

                place_elem = (
                    card.select_one("[data-testid='event-place']")
                    or card.select_one("[class*='place']")
                    or card.select_one("[class*='location']")
                )
                place = place_elem.get_text(strip=True) if place_elem else ""

                link_elem = card.select_one("a[href]")
                event_url = ""
                if link_elem:
                    href = link_elem.get("href", "")
                    event_url = urljoin(base_url, href)

                if topic:
                    searchable = f"{title} {place}".lower()
                    event_data = {"title": title, "place": place, "description": ""}
                    detected = classify_topic_advanced(event_data)
                    if not detected or topic_lower not in detected:
                        if topic_lower not in searchable:
                            continue

                date_str = ru_date(dt)
                events.append(
                    {
                        "title": title,
                        "date": date_str,
                        "date_sort": dt,
                        "place": place or "Москва",
                        "url": event_url if _is_valid_url(event_url) else "",
                        "source": "Яндекс.Афиша",
                    }
                )

            except Exception:
                continue

        await asyncio.sleep(0.5)

    cache_set(ck, events)
    logger.info(f"Yandex Afisha: fetched {len(events)} events")
    return events


async def fetch_tech_business(
    source: dict, start: datetime, end: datetime, max_events: int = 20
) -> list[dict]:
    """Scrape tech/business event sources (Rusbase, VC.ru, Tadviser, etc.)."""
    ck = _cache_key("tech_biz", source["name"], start.strftime("%Y%m%d"))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    events: list[dict] = []
    fetch_kwargs = {"timeout": 20}
    if source.get("follow_redirects"):
        fetch_kwargs["follow_redirects"] = True
    resp = await _fetch_with_retry(source["url"], **fetch_kwargs)
    if not resp:
        return events

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return events

    seen_titles = set()

    # Different selectors for different sites
    event_blocks = soup.find_all(
        ["article", "div", "li"], class_=re.compile(r"event|news|post|item|card", re.I)
    )

    # Fallback: all links with dates
    if not event_blocks:
        event_blocks = soup.find_all("a", href=True)

    for block in event_blocks[:max_events]:
        try:
            # Extract title
            title_elem = block.find(["h2", "h3", "h4"]) or block.find(
                "span", class_=re.compile("title", re.I)
            )
            title = ""
            if title_elem:
                title = title_elem.get_text(strip=True)
            else:
                title = block.get_text(strip=True)

            if not title or len(title) < 10 or len(title) > 200:
                continue

            # Skip duplicates
            title_norm = title.lower()[:80]
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)

            # Extract date
            date_elem = block.find(class_=re.compile("date|time", re.I)) or block.find(
                "time"
            )
            date_text = date_elem.get_text(strip=True) if date_elem else ""
            dt = parse_date_mixed(date_text) or parse_date_mixed(title)

            if not dt:
                continue
            if dt < start or dt > end + timedelta(days=7):
                continue

            # Extract URL
            link = (
                block.get("href") if block.name == "a" else block.find("a", href=True)
            )
            event_url = ""
            if isinstance(link, str):
                event_url = urljoin(source["url"], link)
            elif link and link.get("href"):
                event_url = urljoin(source["url"], link["href"])

            if not event_url:
                continue

            event = {
                "title": title[:200],
                "date": ru_date(dt),
                "date_sort": dt,
                "place": source["name"],
                "url": event_url if _is_valid_url(event_url) else "",
                "source": source["name"],
            }
            event["topic"] = classify_topic_advanced(event)
            events.append(event)

        except Exception:
            continue

    cache_set(ck, events)
    logger.info(f"{source['name']}: fetched {len(events)} events")
    return events


def _parse_ru_day_month(day_str: str, month_str: str, year: int) -> "datetime | None":
    """Парсит русское название месяца + день → datetime."""
    months = {
        "январ": 1, "феврал": 2, "марта": 3, "апрел": 4,
        "мая": 5, "июн": 6, "июл": 7, "август": 8,
        "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    }
    m_lower = month_str.lower().rstrip("яеё").replace("ь", "")
    mo = None
    for k, v in months.items():
        if m_lower.startswith(k) or k.startswith(m_lower):
            mo = v
            break
    if not mo:
        return None
    try:
        return datetime(year, mo, int(day_str))
    except (ValueError, TypeError):
        return None


def _parse_hse_events(soup: BeautifulSoup, url: str, name: str, start: datetime, end: datetime) -> list[dict]:
    """Парсер HSE (ВШЭ): /announcements — ann-cards__group с датами, ann-cards__item с событиями."""
    events: list[dict] = []
    seen_titles: set[str] = set()
    current_date: Optional[datetime] = None

    for group in soup.find_all("div", class_=lambda c: c and "ann-cards__group" in c):
        day_h2 = group.find("h2", class_=lambda c: c and "ann-day" in c)
        if day_h2:
            day_text = day_h2.get_text(" ", strip=True)
            dt = parse_date_mixed(day_text)
            if dt:
                current_date = dt

        for item in group.find_all("div", class_=lambda c: c and "ann-cards__item" in c):
            title_h3 = item.find("h3", class_=lambda c: c and "ann-card__title" in c)
            if not title_h3:
                continue

            title = title_h3.get_text(" ", strip=True)[:200]
            if not title or len(title) < 5:
                continue

            title_norm = title.lower()[:100]
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)

            dt = current_date
            end_dt: Optional[datetime] = None
            time_div = item.find("div", class_=lambda c: c and "ann-cards__time" in c)
            if time_div:
                time_text = time_div.get_text(" ", strip=True)
                dates = re.findall(r"(\d{1,2})[./-](\d{1,2})", time_text)
                if len(dates) >= 2:
                    d1, mo1 = int(dates[0][0]), int(dates[0][1])
                    d2, mo2 = int(dates[1][0]), int(dates[1][1])
                    year = datetime.now().year
                    try:
                        dt = datetime(year, mo1, d1)
                        end_dt = datetime(year, mo2, d2)
                    except ValueError:
                        pass
                elif len(dates) == 1:
                    d, mo = int(dates[0][0]), int(dates[0][1])
                    try:
                        dt = datetime(datetime.now().year, mo, d)
                    except ValueError:
                        pass

            if not dt:
                continue
            start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
            if dt < start_day or dt > end_day:
                continue
            if end_dt and end_dt < start_day:
                continue

            link_tag = title_h3.find("a", href=True) or item.find("a", href=True)
            event_url = urljoin(url, link_tag["href"]) if link_tag else url

            date_str = ru_date(dt)
            events.append({
                "title": title.strip(),
                "date": date_str,
                "date_sort": dt,
                "place": name,
                "url": event_url,
                "source": name,
            })

    return events


def _parse_econ_msu_events(soup, url, name, start, end):
    """Парсер МГУ Экономфак: calendar__item + news-list__card."""
    events = []
    seen_titles = set()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)

    for card in soup.find_all("div", class_=lambda c: c and "calendar__item" in c and "news-list__card" in c):
        title_p = card.find("p", class_=lambda c: c and "calendar__item-title" in c)
        if not title_p:
            continue
        title = title_p.get_text(" ", strip=True)[:200]
        if not title or len(title) < 5:
            continue
        title_norm = title.lower()[:100]
        if title_norm in seen_titles:
            continue
        seen_titles.add(title_norm)

        dt = None
        time_p = card.find("p", class_=lambda c: c and "news-list__events-time" in c)
        if time_p:
            time_text = time_p.get_text(" ", strip=True)
            if "Сегодня" in time_text:
                dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                dt = parse_date_mixed(time_text)

        onclick = card.get("onclick", "")
        link_a = card.find("a", class_=lambda c: c and "arrow-link" in c, href=True)
        event_url = url
        if link_a:
            event_url = urljoin(url, link_a["href"])
        elif onclick and "'" in onclick:
            m = re.search(r"window\.location='([^']+)'", onclick)
            if m:
                event_url = urljoin(url, m.group(1))

        if not dt:
            dt = parse_date_mixed(card.get("title", ""))

        if not dt:
            continue
        if dt < start_day or dt > end_day:
            continue

        date_str = ru_date(dt)
        events.append({
            "title": title.strip(),
            "date": date_str,
            "date_sort": dt,
            "place": name,
            "url": event_url,
            "source": name,
        })

    return events


def _parse_law_msu_events(soup, url, name, start, end):
    """Парсер МГУ Юрфак: calendar-big-day с датой и списком событий."""
    events = []
    seen_titles = set()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    year = datetime.now().year

    for day_div in soup.find_all("div", class_=lambda c: c and "calendar-big-day" in c):
        date_div = day_div.find("div", class_=lambda c: c and "calendar-big-day__date" in c)
        if not date_div:
            continue
        date_text = date_div.get_text(" ", strip=True)
        dt = parse_date_mixed(date_text)
        if not dt:
            m = re.search(r"(\d{1,2})\s+([а-яё]+)", date_text, re.I)
            if m:
                dt = _parse_ru_day_month(m.group(1), m.group(2), year)

        if not dt:
            continue

        event_list = day_div.find("ul", class_=lambda c: c and "calendar-big-day__list" in c)
        if not event_list:
            continue

        for li in event_list.find_all("li"):
            a_tag = li.find("a", href=True)
            if a_tag:
                title = a_tag.get_text(" ", strip=True)[:200]
                event_url = urljoin(url, a_tag["href"])
            else:
                title = li.get_text(" ", strip=True)[:200]
                event_url = url

            if not title or len(title) < 5:
                continue
            title_norm = title.lower()[:100]
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)

            if dt < start_day or dt > end_day:
                continue

            date_str = ru_date(dt)
            events.append({
                "title": title.strip(),
                "date": date_str,
                "date_sort": dt,
                "place": name,
                "url": event_url,
                "source": name,
            })

    return events


def _parse_cs_msu_events(soup, url, name, start, end):
    """Парсер МГУ ВМК: node-news с ISO-датами в content-атрибуте."""
    events = []
    seen_titles = set()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)

    for node in soup.find_all("div", class_=lambda c: c and "node-news" in c):
        h2 = node.find("h2")
        if not h2:
            continue
        a_tag = h2.find("a", href=True)
        if not a_tag:
            continue
        title = a_tag.get_text(" ", strip=True)[:200]
        if not title or len(title) < 5:
            continue
        title_norm = title.lower()[:100]
        if title_norm in seen_titles:
            continue
        seen_titles.add(title_norm)

        event_url = urljoin(url, a_tag["href"])

        dt = None
        period_div = node.find("div", class_=lambda c: c and "field-name-field-period" in c)
        if period_div:
            span = period_div.find("span", attrs={"content": True})
            if span:
                iso = span["content"][:10]
                try:
                    dt = datetime.strptime(iso, "%Y-%m-%d")
                except ValueError:
                    pass
        if not dt:
            dt = parse_date_mixed(period_div.get_text(" ", strip=True))

        if not dt:
            continue
        if dt < start_day or dt > end_day:
            continue

        date_str = ru_date(dt)
        events.append({
            "title": title.strip(),
            "date": date_str,
            "date_sort": dt,
            "place": name,
            "url": event_url,
            "source": name,
        })

    return events


def _parse_rea_events(soup, url, name, start, end):
    """Парсер РЭУ им. Плеханова: catalog__item с русскими датами."""
    events = []
    seen_titles = set()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    year = datetime.now().year

    for item in soup.find_all("div", class_=lambda c: c and "catalog__item" in c):
        title_div = item.find("div", class_=lambda c: c and "catalog__item-title" in c)
        if not title_div:
            continue
        title = title_div.get_text(" ", strip=True)[:200]
        if not title or len(title) < 5:
            continue
        title_norm = title.lower()[:100]
        if title_norm in seen_titles:
            continue
        seen_titles.add(title_norm)

        dt = None
        date_div = item.find("div", class_=lambda c: c and "catalog__item-date" in c)
        if date_div:
            date_text = date_div.get_text(" ", strip=True)
            dates = re.findall(r"(\d{1,2})\s+([а-яё]+)", date_text, re.I)
            if dates:
                dt = _parse_ru_day_month(dates[0][0], dates[0][1], year)
            if not dt:
                dt = parse_date_mixed(date_text)

        link_a = item.find("a", class_=lambda c: c and "catalog__item-content" in c, href=True)
        event_url = urljoin(url, link_a["href"]) if link_a else url

        if not dt:
            continue
        if dt < start_day or dt > end_day:
            continue

        date_str = ru_date(dt)
        events.append({
            "title": title.strip(),
            "date": date_str,
            "date_sort": dt,
            "place": name,
            "url": event_url,
            "source": name,
        })

    return events


def _parse_ranepa_events(soup, url, name, start, end):
    """Парсер РАНХиГС: nl-card с заголовком и русской датой."""
    events = []
    seen_titles = set()
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    year = datetime.now().year

    for card in soup.find_all("a", class_=lambda c: c and "nl-card" in c, href=True):
        heading = card.find("h2", class_=lambda c: c and "nl-card__heading" in c)
        if not heading:
            continue
        title = heading.get_text(" ", strip=True)[:200]
        if not title or len(title) < 5:
            continue
        title_norm = title.lower()[:100]
        if title_norm in seen_titles:
            continue
        seen_titles.add(title_norm)

        dt = None
        date_p = card.find("p", class_=lambda c: c and "nl-card__date" in c)
        if date_p:
            date_text = date_p.get_text(" ", strip=True)
            dates = re.findall(r"(\d{1,2})\s+([а-яё]+)", date_text, re.I)
            if dates:
                dt = _parse_ru_day_month(dates[0][0], dates[0][1], year)
            if not dt:
                dt = parse_date_mixed(date_text)

        event_url = urljoin(url, card["href"])

        if not dt:
            continue
        if dt < start_day or dt > end_day:
            continue

        date_str = ru_date(dt)
        events.append({
            "title": title.strip(),
            "date": date_str,
            "date_sort": dt,
            "place": name,
            "url": event_url,
            "source": name,
        })

    return events


# Специфичные парсеры: URL-паттерн → функция
_UNI_PARSERS = [
    (re.compile(r"hse\.ru", re.I), _parse_hse_events),
    (re.compile(r"econ\.msu\.ru", re.I), _parse_econ_msu_events),
    (re.compile(r"law\.msu\.ru", re.I), _parse_law_msu_events),
    (re.compile(r"cs\.msu\.ru", re.I), _parse_cs_msu_events),
    (re.compile(r"rea\.ru", re.I), _parse_rea_events),
    (re.compile(r"ranepa\.ru", re.I), _parse_ranepa_events),
]


_SSL_SKIP_DOMAINS = {"bmstu.ru"}

async def fetch_university(url: str, name: str, start: datetime, end: datetime) -> list[dict]:
    """Парсит страницу университета через BeautifulSoup с многоуровневой стратегией."""
    # Use improved cache key generation
    ck = cache_manager.generate_key("uni", url=url,
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    from urllib.parse import urlparse
    verify = not any(d in urlparse(url).hostname for d in _SSL_SKIP_DOMAINS if urlparse(url).hostname)

    # Try with retry and better error handling
    try:
        resp = await _fetch_with_retry(url, timeout=20, verify=verify)
        if not resp or not resp.text:
            return []

        html = resp.text

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.warning(f"BeautifulSoup parse error for {name} ({url}): {e}")
            return []

        for pattern, parser_fn in _UNI_PARSERS:
            if pattern.search(url):
                events = parser_fn(soup, url, name, start, end)
                if events:
                    cache_set(ck, events)
                    logger.info(f"{name}: site-specific parser returned {len(events)} events")
                    return events

    except Exception as e:
        logger.warning(f"University fetch error for {name} ({url}): {e}")
        return []

    # Extended skip words
    skip_words = [
        "главная",
        "контакт",
        "о нас",
        "privacy",
        "меню",
        "поделиться",
        "читать далее",
        "подробнее",
        "все новости",
        "абитуриент",
        "сотрудник",
        "преподавател",
        "выпускник",
        "поступ",
        "образован",
        "факультет",
        "институт",
        "кафедр",
        "документ",
        "документы",
        "контактн",
        "режим",
        "версия",
        "электронн",
        "основные сведения",
        "структура",
        "сотрудники",
        "научн",
        "учебн",
        "воспитательн",
        "библиотек",
        "студенч",
        "все события",
        "абитуриентам",
        "сотрудникам",
        "университет",
        "образование",
        "научная жизнь",
        "карта сайта",
        "javascript",
        "войти",
        "поиск",
        "search",
        "версия для слаб",
        "ваканс",
        "приглашаем",
        "работа",
        "трудоустройство",
    ]

    def is_skip(text: str) -> bool:
        return any(s in text.lower() for s in skip_words)

    seen_titles: set[str] = set()
    events: list[dict] = []

    # Strategy 1: Standard event selectors
    for block in soup.find_all(
        ["div", "li", "article", "section"],
        class_=re.compile(
            r"(event|announc|calendar|news|afisha|conference|seminar|item|card|list|feed|post|story)",
            re.I,
        ),
    ):
        raw = block.get_text(separator=" ", strip=True)
        if len(raw) < 15:
            continue

        heading = block.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        title = heading.get_text(strip=True)[:200] if heading else ""

        if not title:
            lines = [l for l in raw.split("\n") if l.strip()]
            title = " ".join(lines[:3])[:200] if lines else raw[:200]

        if not title or is_skip(title):
            continue

        title_norm = title.lower()[:100]
        if title_norm in seen_titles:
            continue
        seen_titles.add(title_norm)

        dt = parse_date_mixed(raw) or parse_date_mixed(title)
        if not dt:
            continue

        if dt < start or dt > end + timedelta(days=7):
            continue

        link_tag = block.find("a", href=True)
        event_url = urljoin(url, link_tag["href"]) if link_tag else url

        date_str = ru_date(dt)
        events.append(
            {
                "title": title.strip(),
                "date": date_str,
                "date_sort": dt,
                "place": name,
                "url": event_url,
                "source": name,
            }
        )

    # Strategy 2: If few results, try extended selectors
    if len(events) < 5:
        for block in soup.find_all(
            ["div", "li", "article", "section"],
            id=re.compile(
                r"(event|announc|calendar|news|afisha|conference|seminar)", re.I
            ),
        ):
            raw = block.get_text(separator=" ", strip=True)
            if len(raw) < 15:
                continue

            lines = [l for l in raw.split("\n") if l.strip()]
            title = " ".join(lines[:2])[:200] if lines else raw[:200]

            if not title or is_skip(title):
                continue

            title_norm = title.lower()[:100]
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)

            dt = parse_date_mixed(raw) or parse_date_mixed(title)
            if not dt:
                continue
            if dt < start or dt > end + timedelta(days=7):
                continue

            link_tag = block.find("a", href=True)
            event_url = urljoin(url, link_tag["href"]) if link_tag else url

            date_str = ru_date(dt)
            events.append(
                {
                    "title": title.strip(),
                    "date": date_str,
                    "date_sort": dt,
                    "place": name,
                    "url": event_url,
                    "source": name,
                }
            )

    # Strategy 3: Fallback - search all links with dates
    if len(events) < 3:
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(separator=" ", strip=True)
            if len(text) < 10 or len(text) > 300:
                continue
            if is_skip(text):
                continue

            dt = parse_date_mixed(text)
            if not dt:
                continue
            if dt < start or dt > end + timedelta(days=7):
                continue

            title_norm = text[:100].lower()
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)

            date_str = ru_date(dt)
            events.append(
                {
                    "title": text[:200],
                    "date": date_str,
                    "date_sort": dt,
                    "place": name,
                    "url": urljoin(url, a_tag["href"]),
                    "source": name,
                }
            )

            if len(events) >= 50:
                break

    cache_set(ck, events)
    return events


# ─── Dedup ───────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\sа-яё]", "", t)
    return re.sub(r"\s+", " ", t)


def dedupe(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for e in events:
        key = f"{_normalize(e['title'])}_{e.get('date', '')}"
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


# ─── Formatter ───────────────────────────────────────────────────────────────


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_text(events: list[dict], city: str, start: datetime, end: datetime) -> str:
    """Telegram-friendly вывод с HTML-ссылками, группировка по датам."""
    if not events:
        return (
            f"📅 <b>{city}</b>\n"
            f"🗓 {start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}\n\n"
            "Ничего не найдено за этот период. Попробуйте изменить запрос."
        )

    grouped: dict[str, list[dict]] = {}
    for e in events:
        dk = e.get("date", "Дата не указана")
        if dk not in grouped:
            grouped[dk] = []
        grouped[dk].append(e)

    lines: list[str] = []
    lines.append(f"📅 <b>{city}</b>")
    lines.append(f"🗓 {start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}")
    lines.append("")

    sorted_dates = sorted(
        grouped.items(), key=lambda x: x[1][0].get("date_sort") or datetime.max
    )

    total = 0
    for date_key, evts in sorted_dates:
        lines.append(f"<b>📆 {date_key}</b>")
        for ev in evts:
            title = _html_escape(ev["title"])
            url = ev.get("url", "")
            time_info = ev.get("time", "")
            time_str = f"  🕐 {time_info}" if time_info else ""
            if url:
                lines.append(f'  ▪️ <a href="{url}">{title}</a>{time_str}')
            else:
                lines.append(f"  ▪️ {title}{time_str}")
            total += 1
        lines.append("")

    lines.append(f"📊 <b>Итого: {total} мероприятий</b>")
    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description=f"Event Finder v{VERSION}")
    parser.add_argument("--topic", "-t", default="бизнес", help="Тематика")
    parser.add_argument("--city", "-c", default="москва", help="Город")
    parser.add_argument("--start-date", "-s", default=None, help="DD.MM.YYYY")
    parser.add_argument("--end-date", "-e", default=None, help="DD.MM.YYYY")
    parser.add_argument(
        "--include-universities", "-u", action="store_true", default=True
    )
    parser.add_argument("--no-universities", "-U", action="store_true", default=False)
    parser.add_argument(
        "--include-yandex",
        "-y",
        action="store_true",
        default=True,
        help="Include Yandex Afisha",
    )
    parser.add_argument(
        "--days", "-d", type=int, default=30, help="Days ahead to search"
    )
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Setup logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # City
    city_raw = args.city.lower().strip()
    city_slug = CITY_MAP.get(city_raw, "msk")
    city_name = city_raw.capitalize()
    is_moscow = city_raw == "москва"

    # Dates
    start = parse_date_mixed(args.start_date) if args.start_date else datetime.now()
    end = (
        parse_date_mixed(args.end_date)
        if args.end_date
        else (start + timedelta(days=args.days))
    )

    print(f"🔍 Event Finder v{VERSION}")
    print(f"📍 Город: {city_name}")
    print(f"📅 Период: {start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}")
    print(f"🎯 Тема: {args.topic}")
    print("⏳ Ищем события...\n")

    all_events: list[dict] = []
    stats = {}

    # 1. KudaGo with pagination
    print("📡 KudaGo API...", end=" ", flush=True)
    k = await fetch_kudago(city_slug, args.topic, start, end, max_events=300)
    all_events.extend(k)
    stats["KudaGo"] = len(k)
    print(f"✅ {len(k)} событий")

    # 2. Timepad with pagination
    print("📡 Timepad API...", end=" ", flush=True)
    t = await fetch_timepad(city_slug, args.topic, start, end, max_events=200)
    all_events.extend(t)
    stats["Timepad"] = len(t)
    print(f"✅ {len(t)} событий")

    # 3. Universities — только Москва
    if is_moscow and not args.no_universities:
        print(f"🏛 Вузы ({len(UNIVERSITIES)} источников)...", end=" ", flush=True)
        uni_events = []
        for uni in UNIVERSITIES:
            u = await fetch_university(uni["url"], uni["name"], start, end)
            uni_events.extend(u)
            await asyncio.sleep(0.3)  # Be polite
        all_events.extend(uni_events)
        stats["Университеты"] = len(uni_events)
        print(f"✅ {len(uni_events)} событий")

    # 4. Yandex Afisha — только Москва
    if is_moscow and args.include_yandex:
        print("📡 Яндекс.Афиша...", end=" ", flush=True)
        y = await fetch_yandex_afisha(args.topic, start, end, max_events=100)
        all_events.extend(y)
        stats["Яндекс.Афиша"] = len(y)
        print(f"✅ {len(y)} событий")

    # Deduplication and sorting
    print("\n🔄 Обработка данных...", end=" ", flush=True)
    before_dedup = len(all_events)
    all_events = dedupe(all_events)
    all_events.sort(key=lambda e: e.get("date_sort") or datetime.max)
    print(f"✅ Удалено дубликатов: {before_dedup - len(all_events)}")

    # Apply advanced topic classification
    for ev in all_events:
        if not ev.get("topic"):
            ev["topic"] = classify_topic_advanced(ev)

    # Output
    print("\n📊 Статистика по источникам:")
    for source, count in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"   • {source}: {count}")
    print(f"\n📋 Всего уникальных событий: {len(all_events)}")

    if args.json:
        serializable_events = _serialize_events(all_events)
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "topic": args.topic,
                    "city": city_name,
                    "date_range": {
                        "start": start.strftime("%Y-%m-%d"),
                        "end": end.strftime("%Y-%m-%d"),
                    },
                    "stats": stats,
                    "total": len(all_events),
                    "events": serializable_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("\n" + "=" * 50)
        print(format_text(all_events, city_name, start, end))


if __name__ == "__main__":
    asyncio.run(main())
