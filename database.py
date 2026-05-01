import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, update, delete, func, desc, asc, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from models import Base, Event, SchedulerState, ScanLog

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "events.db"
DB_PATH.parent.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Global engine and sessionmaker
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class EventDatabase:
    """Async SQLAlchemy database for event storage and scheduler state."""

    async def init_db(self):
        """Create tables if they don't exist."""
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            try:
                await session.execute(text(
                    "DELETE FROM events WHERE id NOT IN ("
                    "  SELECT MIN(id) FROM events GROUP BY title, date, source"
                    ")"
                ))
                await session.commit()
            except Exception:
                await session.rollback()

            try:
                await session.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_event_title_date_source "
                    "ON events (title, date, source)"
                ))
                await session.commit()
                logger.info("Migration: ensured unique index on events(title, date, source)")
            except Exception:
                await session.rollback()

            try:
                await session.execute(text("ALTER TABLE events ADD COLUMN is_llm_reported INTEGER DEFAULT 0"))
                await session.commit()
                logger.info("Migration: added is_llm_reported column to events table.")
            except Exception:
                await session.rollback()

            result = await session.execute(select(SchedulerState).where(SchedulerState.id == 1))
            state = result.scalar_one_or_none()
            if not state:
                state = SchedulerState(id=1)
                session.add(state)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
            logger.info(f"Async Database initialized: {DB_PATH}")

    # ─── Event operations ────────────────────────────────────────────────

    async def save_event(self, event_data: dict) -> Optional[int]:
        """Save event, return inserted row ID or None if duplicate."""
        date_sort = event_data.get("date_sort")
        if isinstance(date_sort, datetime):
            date_sort = date_sort.isoformat()

        async with async_session() as session:
            new_event = Event(
                title=event_data["title"],
                date=event_data.get("date", ""),
                date_sort=date_sort,
                place=event_data.get("place", ""),
                url=event_data.get("url", ""),
                source=event_data.get("source", ""),
                topic=event_data.get("topic", "")
            )
            session.add(new_event)
            try:
                await session.commit()
                return new_event.id
            except IntegrityError:
                await session.rollback()
                return None

    async def save_events_with_ids(self, events: list[dict]) -> list[dict]:
        """Save multiple events in one transaction using INSERT OR IGNORE.

        Returns list of newly inserted events enriched with DB IDs.
        ~5x faster than per-event save_event() for large batches.
        """
        if not events:
            return []

        inserted_events: list[dict] = []

        async with async_session() as session:
            for event in events:
                date_sort = event.get("date_sort")
                if isinstance(date_sort, datetime):
                    date_sort = date_sort.isoformat()

                row = {
                    "title": event["title"],
                    "date": event.get("date", ""),
                    "date_sort": date_sort,
                    "place": event.get("place", ""),
                    "url": event.get("url", ""),
                    "source": event.get("source", ""),
                    "topic": event.get("topic", ""),
                }
                stmt = sqlite_insert(Event).values(**row)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["title", "date", "source"]
                )
                result = await session.execute(stmt)
                if result.rowcount and result.rowcount > 0:
                    saved = dict(event)
                    saved["id"] = result.lastrowid
                    inserted_events.append(saved)

            await session.commit()

        return inserted_events

    async def get_new_events(self, limit: int = 100) -> list[dict]:
        """Get events that haven't been reported yet."""
        async with async_session() as session:
            stmt = select(Event).where(Event.is_new == 1).order_by(asc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_unreported_llm_events(self, limit: int = 500) -> list[dict]:
        """Get events that haven't been reported by the hourly LLM reporter yet."""
        async with async_session() as session:
            stmt = select(Event).where(Event.is_llm_reported == 0).order_by(asc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def mark_events_reported(self, event_ids: Optional[list[int]] = None):
        """Mark events as reported."""
        async with async_session() as session:
            if event_ids is not None:
                if not event_ids:
                    return
                stmt = update(Event).where(Event.id.in_(event_ids)).values(is_new=0)
            else:
                stmt = update(Event).where(Event.is_new == 1).values(is_new=0)
            await session.execute(stmt)
            await session.commit()

    async def mark_events_llm_reported(self, event_ids: list[int]):
        """Mark events as reported by LLM."""
        if not event_ids:
            return
        async with async_session() as session:
            stmt = update(Event).where(Event.id.in_(event_ids)).values(is_llm_reported=1)
            await session.execute(stmt)
            await session.commit()

    async def get_events_by_topic(self, topic: str, limit: int = 50) -> list[dict]:
        """Get events filtered by topic."""
        async with async_session() as session:
            stmt = select(Event).where(
                (Event.topic.like(f"%{topic}%")) |
                (Event.source.like(f"%{topic}%")) |
                (Event.title.like(f"%{topic}%"))
            ).order_by(desc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_events_by_source(self, source: str, limit: int = 50) -> list[dict]:
        """Get events filtered by source."""
        async with async_session() as session:
            stmt = select(Event).where(Event.source == source).order_by(desc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_recent_events(self, limit: int = 20) -> list[dict]:
        """Get most recent events."""
        async with async_session() as session:
            stmt = select(Event).order_by(desc(Event.scanned_at)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_upcoming_events(
        self,
        limit: int = 200,
        topic: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[dict]:
        """Get upcoming events ordered by event date."""
        async with async_session() as session:
            stmt = select(Event).where(Event.date_sort != None, Event.date_sort >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
            if topic:
                stmt = stmt.where(Event.topic.like(f"%{topic}%"))
            if source:
                stmt = stmt.where(Event.source == source)

            stmt = stmt.order_by(asc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_all_sources(self) -> list[str]:
        """Get all distinct source names from events table."""
        async with async_session() as session:
            result = await session.execute(select(Event.source).distinct())
            return [r[0] for r in result.all()]

    async def search_events(
        self,
        sources: list[str],
        date_start: datetime,
        date_end: datetime,
        limit: int = 200,
    ) -> list[dict]:
        """Поиск событий по источникам и диапазону дат.

        date_start/date_end нормализуются к началу/концу дня,
        чтобы ISO-сравнение строк корректно включало события в 00:00.
        """
        ds = date_start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        de = date_end.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        async with async_session() as session:
            stmt = select(Event).where(
                Event.date_sort != None,
                Event.date_sort >= ds,
                Event.date_sort <= de,
                Event.source.in_(sources),
            ).order_by(asc(Event.date_sort)).limit(limit)
            result = await session.execute(stmt)
            events = result.scalars().all()
            return [e.to_dict() for e in events]

    async def get_total_count(self) -> int:
        """Get total number of events in database."""
        async with async_session() as session:
            result = await session.execute(select(func.count(Event.id)))
            return result.scalar() or 0

    async def get_new_count(self) -> int:
        """Get count of unreported events."""
        async with async_session() as session:
            result = await session.execute(select(func.count(Event.id)).where(Event.is_new == 1))
            return result.scalar() or 0

    async def get_sources_summary(self) -> list[dict]:
        """Get event count by source."""
        async with async_session() as session:
            stmt = select(Event.source, func.count(Event.id).label('cnt')).group_by(Event.source).order_by(desc('cnt'))
            result = await session.execute(stmt)
            return [{"source": row.source, "cnt": row.cnt} for row in result.all()]

    async def clear_old_events(self, days: int = 7):
        """Remove events older than N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with async_session() as session:
            stmt = delete(Event).where(Event.date_sort < cutoff)
            await session.execute(stmt)
            await session.commit()

    # ─── Scheduler state operations ──────────────────────────────────────

    async def set_running(self, is_running: bool):
        async with async_session() as session:
            await session.execute(update(SchedulerState).where(SchedulerState.id == 1).values(is_running=1 if is_running else 0))
            await session.commit()

    async def is_running(self) -> bool:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState.is_running).where(SchedulerState.id == 1))
            val = result.scalar_one_or_none()
            return bool(val)

    async def update_last_scan(self, event_count: int):
        total = await self.get_total_count()
        async with async_session() as session:
            stmt = update(SchedulerState).where(SchedulerState.id == 1).values(
                last_scan_at=datetime.now().isoformat(),
                last_scan_count=event_count,
                total_events=total
            )
            await session.execute(stmt)
            await session.commit()

    async def set_admin_chat_id(self, chat_id: int):
        async with async_session() as session:
            await session.execute(update(SchedulerState).where(SchedulerState.id == 1).values(admin_chat_id=chat_id))
            await session.commit()

    async def get_admin_chat_id(self) -> Optional[int]:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState.admin_chat_id).where(SchedulerState.id == 1))
            return result.scalar_one_or_none()

    async def set_topics(self, topics: list[str]):
        async with async_session() as session:
            await session.execute(update(SchedulerState).where(SchedulerState.id == 1).values(topics=json.dumps(topics, ensure_ascii=False)))
            await session.commit()

    async def get_topics(self) -> list[str]:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState.topics).where(SchedulerState.id == 1))
            val = result.scalar_one_or_none()
            if val:
                return json.loads(val)
            return ["бизнес", "экономика", "психология", "история"]

    async def set_scan_interval(self, minutes: int):
        async with async_session() as session:
            await session.execute(update(SchedulerState).where(SchedulerState.id == 1).values(scan_interval_minutes=minutes))
            await session.commit()

    async def get_scan_interval(self) -> int:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState.scan_interval_minutes).where(SchedulerState.id == 1))
            val = result.scalar_one_or_none()
            return val if val else 30

    async def get_status(self) -> dict[str, Any]:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState).where(SchedulerState.id == 1))
            state = result.scalar_one_or_none()
            if not state:
                return {}
            
            status = {
                "is_running": bool(state.is_running),
                "last_scan_at": state.last_scan_at,
                "last_scan_count": state.last_scan_count,
                "scan_interval_minutes": state.scan_interval_minutes,
                "admin_chat_id": state.admin_chat_id,
            }
            status["topics"] = json.loads(state.topics) if state.topics else []
            status["total_events"] = await self.get_total_count()
            status["new_events"] = await self.get_new_count()
            status["sources"] = await self.get_sources_summary()
            return status

    async def get_notion_database_id(self) -> Optional[str]:
        async with async_session() as session:
            result = await session.execute(select(SchedulerState.notion_database_id).where(SchedulerState.id == 1))
            return result.scalar_one_or_none()

    async def set_notion_database_id(self, db_id: str):
        async with async_session() as session:
            await session.execute(update(SchedulerState).where(SchedulerState.id == 1).values(notion_database_id=db_id))
            await session.commit()

    async def log_event(self, event_type: str, message: str = "", details: str = ""):
        async with async_session() as session:
            log = ScanLog(event_type=event_type, message=message, details=details)
            session.add(log)
            await session.commit()

    async def get_recent_logs(self, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            stmt = select(ScanLog).order_by(desc(ScanLog.timestamp)).limit(limit)
            result = await session.execute(stmt)
            logs = result.scalars().all()
            return [l.to_dict() for l in logs]

    async def close(self):
        await engine.dispose()
