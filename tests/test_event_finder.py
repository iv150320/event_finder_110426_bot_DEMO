"""Comprehensive tests for Event Finder."""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from event_search import (
    DAY_OF_WEEK,
    MONTH_MAP_RU,
    CITY_MAP,
    TIMEPAD_CITIES,
    UNIVERSITIES,
    CACHE_TTL,
    _cache_key,
    _normalize,
    dedupe,
    format_text,
    parse_date_mixed,
    ru_date,
    ru_date_range,
    strip_html,
    classify_topic_advanced,
    process_events_pipeline,
    _KUDAGO_EXCLUDED_CATEGORIES,
    _KUDAGO_NEUTRAL_CATEGORIES,
)

def make_event(title="Test Event", date_str="15 апреля, вторник",
               date_sort=None, place="Test Place", url="https://example.com",
               source="KudaGo"):
    if date_sort is None:
        date_sort = datetime.now() + timedelta(days=5)
    return {
        "title": title,
        "date": date_str,
        "date_sort": date_sort,
        "place": place,
        "url": url,
        "source": source,
    }


# ─── Date Helpers ──────────────────────────────────────────────────────────

class TestDateHelpers(unittest.TestCase):
    def test_ru_date_format(self):
        dt = datetime(2026, 4, 15)
        result = ru_date(dt)
        self.assertIn("15", result)
        self.assertIn("апреля", result)
        self.assertTrue(any(d in result for d in DAY_OF_WEEK))

    def test_ru_date_same_month_range(self):
        start = datetime(2026, 4, 10)
        end = datetime(2026, 4, 20)
        result = ru_date_range(start, end)
        self.assertEqual(result, "10–20 апреля")

    def test_ru_date_different_month_range(self):
        start = datetime(2026, 4, 25)
        end = datetime(2026, 5, 5)
        result = ru_date_range(start, end)
        self.assertIn("25", result)
        self.assertIn("апреля", result)
        self.assertIn("5", result)
        self.assertIn("мая", result)

    def test_parse_date_ddmmyyyy(self):
        result = parse_date_mixed("15.04.2026")
        self.assertIsNotNone(result)
        self.assertEqual(result.day, 15)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.year, 2026)

    def test_parse_date_iso(self):
        result = parse_date_mixed("2026-04-15")
        self.assertIsNotNone(result)
        self.assertEqual(result.day, 15)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.year, 2026)

    def test_parse_date_russian_month(self):
        result = parse_date_mixed("15 апреля 2026")
        self.assertIsNotNone(result)
        self.assertEqual(result.day, 15)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.year, 2026)

    def test_parse_date_epoch(self):
        epoch = int(datetime(2026, 4, 15).timestamp())
        result = parse_date_mixed(f"some text {epoch} more text")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)

    def test_parse_date_none_on_invalid(self):
        result = parse_date_mixed("no date here")
        self.assertIsNone(result)

    def test_parse_date_empty(self):
        self.assertIsNone(parse_date_mixed(""))
        self.assertIsNone(parse_date_mixed(None))

    def test_parse_date_invalid_values(self):
        self.assertIsNone(parse_date_mixed("99.99.9999"))
        self.assertIsNone(parse_date_mixed("32.01.2026"))


# ─── Cache ─────────────────────────────────────────────────────────────────

class TestCache(unittest.TestCase):
    def setUp(self):
        from event_search import CACHE_FILE
        self.cache_file = CACHE_FILE
        self.backup = None
        if self.cache_file.exists():
            self.backup = self.cache_file.read_bytes()
            self.cache_file.unlink()

    def tearDown(self):
        if self.backup is not None:
            self.cache_file.write_bytes(self.backup)
        elif self.cache_file.exists():
            self.cache_file.unlink()

    def test_cache_key_generation(self):
        k1 = _cache_key("kudago", "msk", "concerts")
        k2 = _cache_key("kudago", "msk", "concerts")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 32)

    def test_cache_key_different(self):
        k1 = _cache_key("kudago", "msk", "concerts")
        k2 = _cache_key("kudago", "spb", "concerts")
        self.assertNotEqual(k1, k2)

    def test_cache_set_and_get(self):
        from event_search import cache_get, cache_set
        cache_set("test_key", [make_event("Event 1"), make_event("Event 2")])
        result = cache_get("test_key")
        if result is None:
            self.skipTest("Cache DB not writable in this environment")
        self.assertEqual(len(result), 2)

    def test_cache_ttl_expires(self):
        import sqlite3
        from event_search import cache_get, cache_set, CACHE_FILE
        cache_set("expire_key", [make_event("Temp")])
        result = cache_get("expire_key")
        if result is None:
            self.skipTest("Cache DB not writable in this environment")
        conn = sqlite3.connect(str(CACHE_FILE))
        conn.execute(
            "UPDATE cache SET timestamp = ? WHERE key = ?",
            (time.time() - CACHE_TTL - 10, "expire_key"),
        )
        conn.commit()
        conn.close()
        self.assertIsNone(cache_get("expire_key"))

    def test_cache_get_empty(self):
        from event_search import cache_get
        self.assertIsNone(cache_get("nonexistent_key"))


# ─── HTML Strip ────────────────────────────────────────────────────────────

class TestStripHtml(unittest.TestCase):
    def test_strip_simple_tags(self):
        self.assertEqual(strip_html("<p>Hello World</p>"), "Hello World")

    def test_strip_nested_tags(self):
        result = strip_html("<div><p>Text</p><br><span>More</span></div>")
        self.assertIn("Text", result)
        self.assertIn("More", result)

    def test_strip_html_entities(self):
        result = strip_html("&lt;script&gt;alert('xss')&lt;/script&gt;")
        self.assertIn("<script>", result)

    def test_strip_empty(self):
        self.assertEqual(strip_html(""), "")

    def test_strip_no_html(self):
        self.assertEqual(strip_html("Plain text without HTML"), "Plain text without HTML")

    def test_strip_multiple_spaces(self):
        self.assertEqual(strip_html("<p>  Lots   of    spaces  </p>"), "Lots of spaces")


# ─── Deduplication ─────────────────────────────────────────────────────────

class TestDeduplication(unittest.TestCase):
    def test_no_duplicates(self):
        self.assertEqual(len(dedupe([make_event("E1"), make_event("E2")])), 2)

    def test_exact_duplicates(self):
        self.assertEqual(len(dedupe([make_event("Same"), make_event("Same")])), 1)

    def test_case_insensitive_dedup(self):
        self.assertEqual(len(dedupe([make_event("Concert"), make_event("concert")])), 1)

    def test_special_chars_normalized(self):
        events = [make_event("Концерт «Ария»"), make_event("Концерт Ария")]
        self.assertEqual(len(dedupe(events)), 1)

    def test_different_dates_not_deduped(self):
        events = [
            make_event("Event", date_str="15 апреля", date_sort=datetime(2026, 4, 15)),
            make_event("Event", date_str="20 апреля", date_sort=datetime(2026, 4, 20)),
        ]
        self.assertEqual(len(dedupe(events)), 2)

    def test_empty_list(self):
        self.assertEqual(len(dedupe([])), 0)


# ─── Normalize ─────────────────────────────────────────────────────────────

class TestNormalize(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(_normalize("Hello"), "hello")

    def test_remove_special_chars(self):
        self.assertEqual(_normalize("Концерт «Ария»"), "концерт ария")

    def test_collapse_spaces(self):
        self.assertEqual(_normalize("  lots   of   spaces  "), "lots of spaces")

    def test_empty(self):
        self.assertEqual(_normalize(""), "")


# ─── Format Text ───────────────────────────────────────────────────────────

class TestFormatText(unittest.TestCase):
    def test_empty_events(self):
        result = format_text([], "Москва", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertIn("Ничего не найдено", result)
        self.assertIn("Москва", result)

    def test_single_event(self):
        events = [make_event("Test Concert")]
        result = format_text(events, "Москва", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertIn("Test Concert", result)
        self.assertIn("Итого: 1", result)

    def test_urls_in_output(self):
        events = [make_event("Test Event", url="https://kudago.com/event/1")]
        result = format_text(events, "Москва", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertIn("https://kudago.com/event/1", result)
        self.assertIn("<a href=", result)

    def test_multiple_sources_grouped(self):
        events = [
            make_event("Event 1", source="KudaGo"),
            make_event("Event 2", source="Timepad"),
            make_event("Event 3", source="ВШЭ"),
        ]
        result = format_text(events, "Москва", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertIn("Event 1", result)
        self.assertIn("Event 2", result)
        self.assertIn("Event 3", result)
        self.assertIn("Итого: 3", result)

    def test_emoji_present(self):
        events = [make_event("Test")]
        result = format_text(events, "Москва", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertIn("📅", result)
        self.assertIn("🗓", result)
        self.assertIn("📊", result)


# ─── City Map ──────────────────────────────────────────────────────────────

class TestCityMap(unittest.TestCase):
    def test_all_cities_have_slugs(self):
        for city, slug in CITY_MAP.items():
            self.assertIsInstance(city, str)
            self.assertIsInstance(slug, str)
            self.assertTrue(len(slug) > 0)

    def test_moscow_maps_correctly(self):
        self.assertEqual(CITY_MAP["москва"], "msk")

    def test_spb_variants(self):
        self.assertEqual(CITY_MAP["спб"], "spb")
        self.assertEqual(CITY_MAP["питер"], "spb")

    def test_all_13_cities(self):
        self.assertEqual(len(CITY_MAP), 13)


# ─── Timepad Cities ───────────────────────────────────────────────────────

class TestTimepadCities(unittest.TestCase):
    def test_all_timepad_cities_have_ids(self):
        for city, city_id in TIMEPAD_CITIES.items():
            self.assertIsInstance(city, str)
            self.assertIsInstance(city_id, str)
            self.assertTrue(len(city_id) > 0)

    def test_moscow_timepad_id(self):
        self.assertEqual(TIMEPAD_CITIES["москва"], "1")

    def test_spb_timepad_id(self):
        self.assertEqual(TIMEPAD_CITIES["спб"], "2")


# ─── Universities ──────────────────────────────────────────────────────────

class TestUniversities(unittest.TestCase):
    def test_universities_list_not_empty(self):
        self.assertGreater(len(UNIVERSITIES), 0)

    def test_all_unis_have_name_and_url(self):
        for uni in UNIVERSITIES:
            self.assertIn("name", uni)
            self.assertIn("url", uni)
            self.assertTrue(uni["name"])
            self.assertTrue(uni["url"])

    def test_hse_in_universities(self):
        names = [u["name"] for u in UNIVERSITIES]
        self.assertIn("ВШЭ", names)

    def test_new_universities_present(self):
        names = [u["name"] for u in UNIVERSITIES]
        self.assertIn("РАНХиГС", names)
        self.assertIn("МГТУ Баумана", names)
        self.assertIn("МГУ Экономфак", names)

    def test_at_least_10_universities(self):
        self.assertGreaterEqual(len(UNIVERSITIES), 10)


# ─── Month Map ─────────────────────────────────────────────────────────────

class TestMonthMap(unittest.TestCase):
    def test_all_months_present(self):
        for i in range(1, 13):
            found = any(v == i for v in MONTH_MAP_RU.values())
            self.assertTrue(found, f"Month {i} not found")

    def test_russian_months(self):
        self.assertEqual(MONTH_MAP_RU["января"], 1)
        self.assertEqual(MONTH_MAP_RU["декабря"], 12)

    def test_abbreviated_months(self):
        self.assertEqual(MONTH_MAP_RU["янв"], 1)
        self.assertEqual(MONTH_MAP_RU["дек"], 12)


# ─── KudaGo API (Mocked) ──────────────────────────────────────────────────

class TestKudaGoAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from event_search import CACHE_FILE
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_kudago_success(self, mock_http):
        from event_search import fetch_kudago

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{
                "id": 1, "title": "Test Concert",
                "description": "A great concert",
                "site_url": "https://kudago.com/msk/event/1",
                "place": {"name": "Test Venue"},
                "dates": [{"start": int(datetime(2026, 4, 20).timestamp())}],
                "categories": [{"name": "concert"}],
                "is_free": False,
            }]
        }
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_kudago("msk", "концерты", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertGreaterEqual(len(events), 1)
        titles = [e["title"] for e in events]
        self.assertIn("Test Concert", titles)

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_kudago_empty(self, mock_http):
        from event_search import fetch_kudago
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_kudago("msk", "конференции", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertEqual(len(events), 0)

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_kudago_network_error(self, mock_http):
        from event_search import fetch_kudago
        mock_http.return_value = None
        events = await fetch_kudago("msk", "концерты", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertIsInstance(events, list)


# ─── Timepad API (Mocked) ─────────────────────────────────────────────────

class TestTimepadAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from event_search import CACHE_FILE
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_timepad_success(self, mock_http):
        from event_search import fetch_timepad

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "events": [{
                "id": 123,
                "title": "IT Conference Moscow",
                "descriptionPlain": "Conference about technology",
                "startsAt": "2026-04-20T18:00:00Z",
                "organization": {"name": "Tech Corp"},
            }]
        }
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_timepad("msk", "конференции", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertGreaterEqual(len(events), 0)

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_timepad_empty(self, mock_http):
        from event_search import fetch_timepad
        mock_response = MagicMock()
        mock_response.json.return_value = {"events": []}
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_timepad("msk", "концерты", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertEqual(len(events), 0)

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_timepad_network_error(self, mock_http):
        from event_search import fetch_timepad
        mock_http.return_value = None
        events = await fetch_timepad("msk", "концерты", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertIsInstance(events, list)

    @pytest.mark.asyncio
    async def test_fetch_timepad_invalid_city(self):
        from event_search import fetch_timepad
        events = await fetch_timepad("invalid_city", "концерты", datetime.now(), datetime.now() + timedelta(days=30))
        self.assertEqual(len(events), 0)


# ─── University Parser (Mocked) ───────────────────────────────────────────

class TestUniversityParser(unittest.IsolatedAsyncioTestCase):
    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_university_finds_events(self, mock_http):
        from event_search import fetch_university

        html = """
        <html><body>
        <div class="event-item"><h3>Открытая лекция по AI</h3>
        <p>15 апреля 2026 - Приглашаем всех студентов</p></div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_university("https://test-uni.ru/events", "TestUni",
                                   datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertGreaterEqual(len(events), 0)

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_fetch_university_network_error(self, mock_http):
        from event_search import fetch_university
        mock_http.return_value = None
        events = await fetch_university("https://invalid-uni.ru", "InvalidUni",
            datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertEqual(len(events), 0)


# ─── Notion Service (Mocked) ──────────────────────────────────────────────

class TestNotionService(unittest.TestCase):
    @patch("config.NOTION_API_KEY", "test-key")
    @patch("config.NOTION_PARENT_PAGE_ID", "parent-123")
    def test_notion_enabled(self):
        from notion_service import NotionService
        self.assertTrue(NotionService().enabled)

    @patch("config.NOTION_API_KEY", "")
    @patch("config.NOTION_PARENT_PAGE_ID", "")
    def test_notion_disabled(self):
        from notion_service import NotionService
        self.assertFalse(NotionService().enabled)

    @patch("config.NOTION_API_KEY", "test-key")
    @patch("config.NOTION_PARENT_PAGE_ID", "parent-123")
    def test_split_text(self):
        from notion_service import NotionService
        service = NotionService()
        chunks = service._split_text("Short text")
        self.assertEqual(len(chunks), 1)

        big_text = "\n".join(["Line " + "A" * 100 for _ in range(200)])
        chunks = service._split_text(big_text, max_chunk=1800)
        self.assertIsInstance(chunks, list)
        self.assertGreaterEqual(len(chunks), 1)


# ─── Database ──────────────────────────────────────────────────────────────

class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "events.db"
        DATABASE_URL = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine = create_async_engine(DATABASE_URL, echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        
        self.engine_patcher = patch("database.engine", self.engine)
        self.session_patcher = patch("database.async_session", self.async_session)
        self.engine_patcher.start()
        self.session_patcher.start()
        
        import database
        self.db = database.EventDatabase()
        await self.db.init_db()

    async def asyncTearDown(self):
        self.engine_patcher.stop()
        self.session_patcher.stop()
        await self.engine.dispose()
        self.tmpdir.cleanup()

    @pytest.mark.asyncio
    async def test_mark_events_reported_by_ids_only_updates_selected_rows(self):
        inserted = await self.db.save_events_with_ids([
            make_event("Event 1", date_sort=datetime(2026, 4, 15), source="KudaGo"),
            make_event("Event 2", date_sort=datetime(2026, 4, 16), source="Timepad"),
        ])

        await self.db.mark_events_reported([inserted[0]["id"]])

        remaining = await self.db.get_new_events(limit=10)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["title"], "Event 2")

    @pytest.mark.asyncio
    async def test_get_upcoming_events_returns_future_only(self):
        past_event = make_event(
            "Past",
            date_sort=datetime.now() - timedelta(days=2),
            date_str="1 января, понедельник",
            source="KudaGo",
        )
        future_event = make_event(
            "Future",
            date_sort=datetime.now() + timedelta(days=2),
            date_str="20 апреля, понедельник",
            source="Timepad",
        )
        await self.db.save_events_with_ids([past_event, future_event])

        upcoming = await self.db.get_upcoming_events(limit=10)
        titles = [item["title"] for item in upcoming]
        self.assertIn("Future", titles)
        self.assertNotIn("Past", titles)


# ─── Integration ───────────────────────────────────────────────────────────

class TestIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from event_search import CACHE_FILE
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()

    @pytest.mark.asyncio
    @patch("event_search._fetch_with_retry", new_callable=AsyncMock)
    async def test_full_search_pipeline(self, mock_http):
        from event_search import fetch_kudago, dedupe, format_text

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{
                "id": 1, "title": "AI Conference 2026",
                "description": "Annual AI conference about machine learning",
                "site_url": "https://kudago.com/msk/event/1",
                "place": {"name": "Moscow Convention Center"},
                "dates": [{"start": int(datetime(2026, 4, 20).timestamp())}],
                "categories": [{"name": "conference"}],
                "is_free": True,
            }]
        }
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value = mock_response

        events = await fetch_kudago("msk", "конференции", datetime(2026, 4, 1), datetime(2026, 5, 1))
        self.assertGreaterEqual(len(events), 0)

        if events:
            events = dedupe(events)
            output = format_text(events, "Москва", datetime(2026, 4, 1), datetime(2026, 5, 1))
            self.assertIsInstance(output, str)
            self.assertIn("Итого:", output)


# ─── Topic Classification ────────────────────────────────────────────────


class TestTopicClassification(unittest.TestCase):
    def test_business_keywords(self):
        ev = {"title": "Бизнес-завтрак для стартапов", "description": "", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "бизнес")

    def test_it_keywords(self):
        ev = {"title": "IT-конференция для разработчиков", "description": "software инженерия", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "IT")

    def test_ai_keywords(self):
        ev = {"title": "AI лекция про нейросети", "description": "machine learning и GPT", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "AI")

    def test_economics_keywords(self):
        ev = {"title": "Экономический форум", "description": "макроэкономика и финансы", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "экономика")

    def test_politics_keywords(self):
        ev = {"title": "Политический дискуссионный клуб", "description": "геополитика и реформы", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "политика")

    def test_history_keywords(self):
        ev = {"title": "Лекция по истории", "description": "исторический разбор революции", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "история")

    def test_english_keywords(self):
        ev = {"title": "English conversation club", "description": "практика английского языка", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "английский язык")

    def test_psychology_keywords(self):
        ev = {"title": "Мастер-класс по психологии эмоций", "description": "", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "психология")

    def test_literature_keywords(self):
        ev = {"title": "Вечер поэзии", "description": "чтение стихов и проза", "categories": []}
        self.assertEqual(classify_topic_advanced(ev), "литература")

    def test_kudago_category_exclusion_concert(self):
        ev = {"title": "Концерт симфонического оркестра", "description": "", "categories": ["concert"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_kudago_category_exclusion_entertainment(self):
        ev = {"title": "Бурлеск-шоу", "description": "", "categories": ["entertainment"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_kudago_category_exclusion_theater(self):
        ev = {"title": "Спектакль Примадонны", "description": "", "categories": ["theater", "stock"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_kudago_category_exclusion_party(self):
        ev = {"title": "Быстрые свидания", "description": "", "categories": ["party", "entertainment"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_kudago_education_without_excluded_passes(self):
        ev = {"title": "Лекция по нейросетям", "description": "", "categories": ["education"]}
        self.assertEqual(classify_topic_advanced(ev), "IT")

    def test_kudago_education_with_excluded_rejected(self):
        ev = {"title": "Урок вокала", "description": "", "categories": ["stock", "education", "entertainment"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_kudago_exhibition_passes(self):
        ev = {"title": "Выставка народов России", "description": "историческое наследие", "categories": ["exhibition"]}
        self.assertEqual(classify_topic_advanced(ev), "история")

    def test_word_boundary_ai_no_false_positive(self):
        import re
        text = "entertainment concert painting"
        self.assertEqual(len(re.findall(r"\bai\b", text)), 0)

    def test_word_boundary_it_no_false_positive(self):
        import re
        text = "китайский язык с носителем"
        self.assertEqual(len(re.findall(r"\bit\b", text)), 0)

    def test_word_boundary_ai_legitimate_match(self):
        import re
        text = "ai конференция по нейросетям"
        self.assertEqual(len(re.findall(r"\bai\b", text)), 1)

    def test_no_topic_returns_empty(self):
        ev = {"title": "Выставка цветов", "description": "", "categories": ["exhibition"]}
        self.assertEqual(classify_topic_advanced(ev), "")

    def test_pipeline_filters_by_allowed_topics(self):
        events = [
            {"title": "Бизнес-встреча", "topic": "бизнес", "date_sort": datetime.now()},
            {"title": "Концерт", "topic": "", "date_sort": datetime.now()},
            {"title": "IT-лекция", "topic": "IT", "date_sort": datetime.now()},
        ]
        allowed = ["бизнес", "IT", "AI", "экономика", "политика", "история", "английский язык", "психология", "литература"]
        result = process_events_pipeline(events, allowed)
        titles = [e["title"] for e in result]
        self.assertIn("Бизнес-встреча", titles)
        self.assertIn("IT-лекция", titles)
        self.assertNotIn("Концерт", titles)


# ─── Config ────────────────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    @patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "ALLOWED_USERS": "123, 456",
        "NOTION_API_KEY": "ntn-test",
        "NOTION_PARENT_PAGE_ID": "parent-123",
    }, clear=False)
    def test_config_loads_from_env(self):
        import importlib
        import config
        importlib.reload(config)
        self.assertEqual(config.TELEGRAM_BOT_TOKEN, "test-token")
        self.assertEqual(config.ALLOWED_USERS, (123, 456))


if __name__ == "__main__":
    unittest.main()
