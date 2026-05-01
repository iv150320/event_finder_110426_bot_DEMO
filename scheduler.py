#!/usr/bin/env python3
"""Scheduler — background event scanning every 30 minutes (v9.2.0).

Scans universities and corporate sources for new events.
Stores them in SQLite database and sends reports to Telegram.
"""

import asyncio
import logging
import time
import random
from datetime import datetime, timedelta
from typing import Callable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from event_search import (
    UNIVERSITIES,
    CORPORATE_SOURCES,
    TECH_BUSINESS_SOURCES,
    
    fetch_kudago,
    fetch_timepad,
    fetch_yandex_afisha,
    fetch_university,
    fetch_tech_business,
    parse_date_mixed,
    ru_date,
    fetch_habr,
    process_events_pipeline,  # New improved processing
)
from database import EventDatabase
from nvidia_service import generate_hourly_llm_report
import config

logger = logging.getLogger(__name__)


class EventScheduler:
    """Background scheduler for periodic event scanning."""

    def __init__(self, db: EventDatabase):
        self.db = db
        self._task: Optional[asyncio.Task] = None
        self._llm_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_scan_time: Optional[datetime] = None
        self._last_scan_count = 0
        self._total_scanned = 0
        self._scan_start_time: Optional[float] = None
        self._on_report: Optional[Callable] = None  # Callback for Telegram notification
        self._on_notion_page: Optional[Callable] = None  # Callback for Notion page URL
        self._topics = []
        self._scan_interval = 30

    def set_on_report(self, callback: Callable):
        """Set callback for when a report is ready."""
        self._on_report = callback

    def set_on_notion_page(self, callback: Callable):
        """Set callback for when a Notion page is created."""
        self._on_notion_page = callback

    async def start(self):
        """Start the scheduler."""
        if self._running:
            logger.info("Scheduler is already running")
            return

        self._running = True
        await self.db.set_running(True)
        logger.info(f"Scheduler started (interval: {self._scan_interval} min)")

        self._task = asyncio.create_task(self._run_loop())
        self._llm_task = asyncio.create_task(self._llm_report_loop())

    async def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return

        self._running = False
        await self.db.set_running(False)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._llm_task:
            self._llm_task.cancel()
            try:
                await self._llm_task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    def is_running(self) -> bool:
        return self._running

    async def _run_loop(self):
        """Main scanning loop."""
        await self.db.log_event(
            "START", "Scheduler loop started", f"Interval: {self._scan_interval} min"
        )

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"Scan cycle error: {e}")
                await self.db.log_event("ERROR", f"Scan cycle error: {e}")

            # Wait for next interval
            for _ in range(self._scan_interval * 60):  # Convert to seconds
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _llm_report_loop(self):
        """Hourly loop to generate LLM summary for new events."""
        await asyncio.sleep(60) # Initial short delay so it doesn't run concurrently with the very first scan immediately
        
        while self._running:
            try:
                unreported = await self.db.get_unreported_llm_events(limit=500)
                if unreported:
                    logger.info(f"LLM Reporter: Found {len(unreported)} unreported events.")
                    
                    # Format for LLM
                    events_text = "\n".join(f"- {e.get('title', 'Без названия')} ({e.get('date', '')}) [Источник: {e.get('source', '')}]" for e in unreported)
                    
                    # Call DeepSeek
                    report_text = await generate_hourly_llm_report(events_text)
                    
                    if report_text and "Нет новых релевантных событий" not in report_text and self._on_report:
                        header = f"🤖 **Ежечасный Дайджест от DeepSeek**\n\n"
                        await self._on_report(header + report_text)
                        
                    # Mark them as reported by LLM so they don't appear in the next hour
                    event_ids = [e['id'] for e in unreported if e.get('id') is not None]
                    if event_ids:
                        await self.db.mark_events_llm_reported(event_ids)
                        
            except Exception as e:
                logger.error(f"LLM report cycle error: {e}")

            # Wait for 1 hour (3600 seconds)
            for _ in range(3600):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _scan_cycle(self):
        self._scan_interval = await self.db.get_scan_interval()
        self._topics = await self.db.get_topics()
        """Single scan cycle: scan all sources, store new events, send report."""
        self._scan_start_time = time.time()
        await self.db.log_event("SCAN_START", "Starting scan cycle")
        logger.info(f"Starting scan cycle at {datetime.now().strftime('%H:%M:%S')}")

        all_events: list[dict] = []
        today = datetime.now()
        end = today + timedelta(days=30)

        cities = config.SCHEDULER_CITIES or ["msk"]

        # 1. Scan KudaGo API (raw events without classification)
        logger.info("Scanning KudaGo API...")
        for city_slug in cities:
            try:
                raw_events = await fetch_kudago(city_slug, "", today, end, 200)
                all_events.extend(raw_events)
                logger.info(f"Found {len(raw_events)} raw events from KudaGo ({city_slug})")
            except Exception as e:
                logger.warning(f"KudaGo scan error ({city_slug}): {e}")

        # 2. Scan Timepad API (raw events)
        logger.info("Scanning Timepad API...")
        for city_slug in cities:
            try:
                raw_events = await fetch_timepad(city_slug, "", today, end, 100)
                all_events.extend(raw_events)
                logger.info(f"Found {len(raw_events)} raw events from Timepad ({city_slug})")
            except Exception as e:
                logger.warning(f"Timepad scan error ({city_slug}): {e}")

        # 3. Scan Yandex Afisha (raw events)
        logger.info("Scanning Yandex Afisha...")
        try:
            raw_events = await fetch_yandex_afisha("", today, end, 50)
            all_events.extend(raw_events)
            logger.info(f"Found {len(raw_events)} raw events from Yandex Afisha")
            await asyncio.sleep(random.uniform(1.0, 3.0))
        except Exception as e:
            logger.warning(f"Yandex Afisha scan error: {e}")

        # 4. Scan universities (raw events)
        logger.info(f"Scanning {len(UNIVERSITIES)} universities...")
        async def fetch_uni_safe(uni, today, end):
            try:
                res = await fetch_university(uni["url"], uni["name"], today, end)
                logger.info(f"Found {len(res)} raw events from {uni['name']}")
                return res
            except Exception as e:
                logger.warning(f"University scan error for {uni['name']}: {e}")
                return []
        
        uni_tasks = [fetch_uni_safe(uni, today, end) for uni in UNIVERSITIES]
        uni_results = await asyncio.gather(*uni_tasks)
        for res in uni_results:
            all_events.extend(res)
        logger.info("Done scanning universities")

        # 5. Scan corporate sources (raw events)
        logger.info(f"Scanning {len(CORPORATE_SOURCES)} corporate sources...")
        async def fetch_corp_safe(corp, today, end):
            try:
                res = await self._scan_corporate_source(corp, today, end)
                logger.info(f"Found {len(res)} raw events from {corp['name']}")
                return res
            except Exception as e:
                logger.warning(f"Corporate scan error for {corp['name']}: {e}")
                return []
        
        corp_tasks = [fetch_corp_safe(corp, today, end) for corp in CORPORATE_SOURCES]
        corp_results = await asyncio.gather(*corp_tasks)
        for res in corp_results:
            all_events.extend(res)
        logger.info("Done scanning corporate sources")

        # 6. Scan tech/business sources (raw events)
        logger.info(f"Scanning {len(TECH_BUSINESS_SOURCES)} tech/business sources...")
        async def fetch_tech_safe(source, today, end):
            try:
                res = await fetch_tech_business(source, today, end, 20)
                logger.info(f"Found {len(res)} raw events from {source['name']}")
                return res
            except Exception as e:
                logger.warning(f"Tech business scan error for {source['name']}: {e}")
                return []
        
        tech_tasks = [fetch_tech_safe(source, today, end) for source in TECH_BUSINESS_SOURCES]
        tech_results = await asyncio.gather(*tech_tasks)
        for res in tech_results:
            all_events.extend(res)
        logger.info("Done scanning tech/business sources")

        # Scan Habr Events
        logger.info("Scanning Habr...")
        try:
            raw_events = await fetch_habr("", today, end, 50)
            all_events.extend(raw_events)
            logger.info(f"Found {len(raw_events)} raw events from Habr")
            await asyncio.sleep(random.uniform(1.0, 3.0))
        except Exception as e:
            logger.warning(f"Habr scan error: {e}")

    # 7. Clean up old events
        cleanup_days = config.SCHEDULER_CLEANUP_DAYS
        try:
            await self.db.clear_old_events(days=cleanup_days)
        except Exception as e:
            logger.warning(f"Old events cleanup error: {e}")

        logger.info(f"Total raw events found: {len(all_events)}")

        # NEW: Process events through improved pipeline
        allowed_topics = config.SCHEDULER_ALLOWED_TOPICS
        processed_events = process_events_pipeline(all_events, allowed_topics)

        logger.info(f"After processing: {len(processed_events)} events (dedup + classification + filtering)")

        if allowed_topics and len(all_events) > len(processed_events):
            skipped = len(all_events) - len(processed_events)
            logger.info(f"Topic filter: skipped {skipped} events not in allowed topics")

        all_events = processed_events

        # Save to database
        inserted_events = await self.db.save_events_with_ids(all_events)
        new_count = len(inserted_events)
        self._last_scan_count = new_count
        self._total_scanned += len(all_events)
        self._last_scan_time = datetime.now()

        # 7. Update DB state
        await self.db.update_last_scan(len(all_events))

        scan_duration = time.time() - self._scan_start_time
        logger.info(
            f"Scan complete: {len(all_events)} total, {new_count} new, "
            f"duration: {scan_duration:.1f}s"
        )
        await self.db.log_event(
            "SCAN_COMPLETE",
            f"Scan complete: {new_count} new",
            f"Duration: {scan_duration:.1f}s, Total: {len(all_events)}",
        )

        # 7. Add events to Notion Database automatically (only if there are new events)
        notion_db_id = ""
        if new_count > 0 and self._on_notion_page:
            try:
                notion_db_id = await self._on_notion_page(
                    inserted_events, scan_duration
                )
            except Exception as e:
                logger.error(f"Notion auto-create error: {e}")
                await self.db.log_event("ERROR", f"Notion error: {e}")

        # 8. Send report to Telegram — only if there are NEW events
        if new_count > 0 and self._on_report:
            report = await self._build_report(
                new_count, inserted_events, scan_duration, notion_db_id
            )
            await self._on_report(report)

    async def _build_report(
        self, new_count: int, events: list[dict], duration: float, notion_url: str = ""
    ) -> str:
        """Build a formatted report message (только новые события)."""
        header = f"📊 <b>Найдено {new_count} новых событий</b>\n"

        lines = [
            header,
            f"⏱ Сканирование: {duration:.1f} сек\n",
        ]

        if notion_url:
            lines.append(f'📤 <a href="{notion_url}">Открыть таблицу в Notion</a>\n')

        # Группировка по теме
        by_topic: dict[str, list[dict]] = {}
        for ev in events:
            topic = ev.get("topic", "другое")
            if topic not in by_topic:
                by_topic[topic] = []
            by_topic[topic].append(ev)

        for topic in sorted(by_topic.keys()):
            topic_events = by_topic[topic][:5]
            lines.append(f"\n🎯 <b>{topic.capitalize()}</b> ({len(by_topic[topic])})")

            for ev in topic_events:
                title = ev.get("title", "Без названия")[:120]
                date = ev.get("date", "")
                url = ev.get("url", "")

                if url:
                    lines.append(f'  ▪️ <a href="{url}">{title}</a>')
                else:
                    lines.append(f"  ▪️ {title}")

                if date:
                    lines[-1] += f" — {date}"

            if len(by_topic[topic]) > 5:
                lines.append(f"  ... и ещё {len(by_topic[topic]) - 5}")

        lines.append(f"\n📊 Всего в базе: {await self.db.get_total_count()} событий")
        return "\n".join(lines)

    async def get_status_text(self) -> str:
        """Get current scheduler status as text."""
        status = await self.db.get_status()
        if not status:
            return "❌ Статус недоступен"

        running = "🟢 Работает" if status.get("is_running") else "🔴 Остановлен"
        last_scan = status.get("last_scan_at") or "Ещё не сканировал"
        topics = ", ".join(status.get("topics", []))

        lines = [
            "📊 <b>Статус планировщика</b>\n",
            f"Состояние: {running}",
            f"Интервал: {status.get('scan_interval_minutes', 30)} мин",
            f"Последнее сканирование: {last_scan}",
            f"Найдено за цикл: {status.get('last_scan_count', 0)}",
            f"Новых событий: {status.get('new_events', 0)}",
            f"Всего в базе: {status.get('total_events', 0)}",
            f"\n🎯 Темы: {topics}",
        ]

        # Sources summary
        sources = status.get("sources", [])
        if sources:
            lines.append("\n📚 Источники:")
            for s in sources[:10]:
                lines.append(f"  • {s['source']}: {s['cnt']}")

        return "\n".join(lines)

    async def _scan_corporate_source(
        self, source: dict, start: datetime, end: datetime
    ) -> list[dict]:
        """Scan a corporate news page for events."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(source["url"])
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Corporate fetch error for {source['name']}: {e}")
            return []

        html_text = resp.text
        events: list[dict] = []

        try:
            soup = BeautifulSoup(html_text, "html.parser")
        except Exception:
            return []

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
            "подписаться",
            "архив",
            "каталог",
            "назад",
            "вперёд",
        ]

        def is_skip(text: str) -> bool:
            return any(s in text.lower() for s in skip_words)

        # Find article blocks
        target_blocks = soup.find_all(
            ["article", "div", "li"],
            class_=lambda c: (
                c
                and any(
                    kw in c.lower()
                    for kw in [
                        "news",
                        "article",
                        "post",
                        "story",
                        "item",
                        "card",
                        "press",
                    ]
                )
            ),
        )

        if not target_blocks:
            target_blocks = soup.find_all(
                ["article", "div", "li"],
                id=lambda i: (
                    i
                    and any(
                        kw in i.lower()
                        for kw in [
                            "news",
                            "article",
                            "post",
                            "story",
                            "item",
                            "card",
                            "press",
                        ]
                    )
                ),
            )

        for block in target_blocks:
            raw = block.get_text(separator=" ", strip=True)
            if len(raw) < 15:
                continue

            heading = block.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text(strip=True)[:200]
            else:
                lines = [l for l in raw.split("\n") if l.strip()]
                title = " ".join(lines[:2])[:200] if lines else raw[:200]

            if not title or is_skip(title):
                continue

            dt = parse_date_mixed(raw)
            if not dt:
                dt = parse_date_mixed(title)
            if not dt:
                continue

            if dt < start or dt > end + timedelta(days=7):
                continue

            link_tag = block.find("a", href=True)
            if link_tag:
                event_url = urljoin(source["url"], link_tag["href"])
            else:
                event_url = source["url"]

            date_str = ru_date(dt)
            events.append(
                {
                    "title": title.strip(),
                    "date": date_str,
                    "date_sort": dt,
                    "place": source["name"],
                    "url": event_url,
                    "source": source["name"],
                }
            )

        return events
