#!/usr/bin/env python3
"""Notion service — создание страниц с результатами поиска мероприятий."""

import asyncio
import logging
import time as _time
from datetime import datetime
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class NotionService:
    """Сервис для создания страниц в Notion с результатами поиска."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self):
        self.api_key = config.NOTION_API_KEY
        self.parent_page_id = config.NOTION_PARENT_PAGE_ID
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        })
        self.timeout = 15

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.parent_page_id)

    def create_event_page(
        self,
        topic: str,
        city: str,
        events_text: str,
        search_date: str,
    ) -> Optional[str]:
        """Создать страницу в Notion с результатами поиска."""
        if not self.enabled:
            logger.warning("Notion not configured (missing API key or parent page ID)")
            return None

        try:
            # Parse date for title
            try:
                dt = datetime.fromisoformat(search_date)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

            title = f"📅 {topic.capitalize()} в г. {city} — {date_str}"

            # Split text into chunks (Notion block limit ~2000 chars per block)
            chunks = self._split_text(events_text)

            # Build page blocks
            blocks = []
            for i, chunk in enumerate(chunks):
                if i == 0:
                    # First block as heading
                    blocks.append({
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"type": "text", "text": {"content": title}}]
                        }
                    })
                    # Metadata
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": f"Тематика: {topic} | Город: {city} | Дата поиска: {date_str}"
                                    }
                                }
                            ]
                        }
                    })
                    blocks.append({
                        "object": "block",
                        "type": "divider",
                        "divider": {}
                    })
                else:
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}]
                        }
                    })

            # Create page
            payload = {
                "parent": {"type": "page_id", "page_id": self.parent_page_id},
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
                "children": blocks,
            }

            resp = self._session.post(
                f"{self.BASE_URL}/pages",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            page_url = data.get("url", "")
            logger.info(f"Notion page created: {page_url}")
            return page_url

        except requests.exceptions.HTTPError as e:
            logger.error(f"Notion HTTP error: {e.response.text if e.response else str(e)}")
            return None
        except Exception as e:
            logger.error(f"Notion error: {e}")
            return None

    def _split_text(self, text: str, max_chunk: int = 1800) -> list[str]:
        """Разбить текст на чанки для блоков Notion."""
        if not text:
            return []
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_chunk:
                if current:
                    chunks.append(current.strip())
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current.strip())
        return chunks

    def create_events_table_page(
        self,
        title: str,
        events: list[dict],
        scan_date: str,
    ) -> Optional[str]:
        """Legacy method — creates a new page with a table block (for manual trigger)."""
        if not self.enabled:
            logger.warning("Notion not configured")
            return None

        try:
            blocks = []
            # Title
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": f"📅 {title}"}}]}
            })
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Дата сканирования: {scan_date}"}}]}
            })
            blocks.append({"object": "block", "type": "divider", "divider": {}})

            # Group by source
            by_source: dict[str, list[dict]] = {}
            for ev in events:
                src = ev.get("source", "Другое")
                if src not in by_source: by_source[src] = []
                by_source[src].append(ev)

            for source, src_events in sorted(by_source.items()):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"{source} ({len(src_events)})"}}]}
                })
                # Table block
                blocks.append({
                    "object": "block",
                    "type": "table",
                    "table": {
                        "table_width": 3,
                        "has_column_header": True,
                        "has_row_header": False,
                        "children": [
                            {
                                "type": "table_row",
                                "table_row": {
                                    "cells": [
                                        [{"type": "text", "text": {"content": "Дата"}}],
                                        [{"type": "text", "text": {"content": "Событие"}}],
                                        [{"type": "text", "text": {"content": "Ссылка"}}],
                                    ]
                                }
                            },
                            *[
                                {
                                    "type": "table_row",
                                    "table_row": {
                                        "cells": [
                                            [{"type": "text", "text": {"content": ev.get("date", "")[:30]}}],
                                            [{"type": "text", "text": {"content": ev.get("title", "")[:100]}}],
                                            [{"type": "text", "text": {"content": ev.get("url", "")[:80]}}],
                                        ]
                                    }
                                }
                                for ev in src_events[:20]
                            ],
                        ],
                    },
                })
                blocks.append({"object": "block", "type": "divider", "divider": {}})

            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Всего: {len(events)} событий"}}]}
            })

            payload = {
                "parent": {"type": "page_id", "page_id": self.parent_page_id},
                "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
                "children": blocks,
            }

            resp = self._session.post(f"{self.BASE_URL}/pages", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            page_url = resp.json().get("url", "")
            logger.info(f"Notion page created: {page_url}")
            return page_url
        except Exception as e:
            logger.error(f"Notion table page error: {e}")
            return None

    async def ensure_events_database(self, db_helper) -> Optional[str]:
        """Create a Notion database (inline in root page) if it doesn't exist, return its ID."""
        if not self.enabled:
            return None

        db_id = await db_helper.get_notion_database_id()
        if db_id:
            return db_id

        logger.info("Creating Notion events database in root page...")
        try:
            payload = {
                "parent": {"type": "page_id", "page_id": self.parent_page_id},
                "title": [{"type": "text", "text": {"content": "📅 Event Finder — Все события"}}],
                "properties": {
                    "Событие": {"title": {}},
                    "Дата": {"date": {}},
                    "Источник": {"rich_text": {}},
                    "Тема": {"rich_text": {}},
                    "Ссылка": {"url": {}},
                },
            }

            def _sync_create():
                resp = self._session.post(
                    f"{self.BASE_URL}/databases", json=payload, timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.json()

            data = await asyncio.to_thread(_sync_create)
            db_id = data.get("id")

            if db_id:
                await db_helper.set_notion_database_id(db_id)
                logger.info(f"Notion database created: {db_id}")
            return db_id
        except Exception as e:
            logger.error(f"Notion DB creation error: {e}")
            return None

    NOTION_RATE_LIMIT = 0.4
    NOTION_BATCH_SIZE = 20

    def add_events_to_database(self, database_id: str, events: list[dict]) -> bool:
        """Add events as rows to the Notion database (with throttling)."""
        if not database_id:
            return False

        added_count = 0
        for i, ev in enumerate(events):
            try:
                if i > 0 and i % self.NOTION_BATCH_SIZE == 0:
                    _time.sleep(1.0)
                elif i > 0:
                    _time.sleep(self.NOTION_RATE_LIMIT)

                date_val = None
                dt = ev.get("date_sort")
                if isinstance(dt, str):
                    try:
                        date_val = dt.split("T")[0]
                    except (ValueError, AttributeError):
                        pass
                elif hasattr(dt, 'strftime'):
                    date_val = dt.strftime("%Y-%m-%d")

                props = {
                    "Событие": {"title": [{"text": {"content": ev.get("title", "")[:2000]}}]},
                }
                if date_val:
                    props["Дата"] = {"date": {"start": date_val}}

                source = ev.get("source", "Другое")
                if source: props["Источник"] = {"rich_text": [{"text": {"content": source[:2000]}}]}

                topic = ev.get("topic", "")
                if topic: props["Тема"] = {"rich_text": [{"text": {"content": topic[:2000]}}]}

                url = ev.get("url", "")
                if url: props["Ссылка"] = {"url": url}

                payload = {
                    "parent": {"database_id": database_id},
                    "properties": props,
                }

                resp = self._session.post(f"{self.BASE_URL}/pages", json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    added_count += 1
                else:
                    logger.warning(f"Notion add row failed ({resp.status_code}): {ev.get('title')}")
            except Exception as e:
                logger.error(f"Notion row error: {e}")
        
        logger.info(f"Added {added_count} rows to Notion database")
        return added_count > 0
