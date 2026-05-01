from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = 'events'
    __table_args__ = (
        UniqueConstraint('title', 'date', 'source', name='uq_event_title_date_source'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[Optional[str]] = mapped_column(String)
    date_sort: Mapped[Optional[str]] = mapped_column(String)
    place: Mapped[Optional[str]] = mapped_column(String)
    url: Mapped[Optional[str]] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String, default='', index=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    is_new: Mapped[int] = mapped_column(Integer, default=1, index=True)
    is_llm_reported: Mapped[int] = mapped_column(Integer, default=0, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "date_sort": self.date_sort,
            "place": self.place,
            "url": self.url,
            "source": self.source,
            "topic": self.topic,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
            "is_new": self.is_new,
            "is_llm_reported": self.is_llm_reported,
        }

class SchedulerState(Base):
    __tablename__ = 'scheduler_state'

    id: Mapped[int] = mapped_column(Integer, primary_key=True) # Always 1
    is_running: Mapped[int] = mapped_column(Integer, default=0)
    last_scan_at: Mapped[Optional[str]] = mapped_column(String)
    last_scan_count: Mapped[int] = mapped_column(Integer, default=0)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    admin_chat_id: Mapped[Optional[int]] = mapped_column(Integer)
    topics: Mapped[str] = mapped_column(String, default='["бизнес", "экономика", "психология", "история"]')
    notion_database_id: Mapped[Optional[str]] = mapped_column(String)

class ScanLog(Base):
    __tablename__ = 'scan_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(String)
    details: Mapped[Optional[str]] = mapped_column(String)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "message": self.message,
            "details": self.details,
        }

class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class Subscription(Base):
    __tablename__ = 'subscriptions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True) # Foreign key conceptually
    topics: Mapped[str] = mapped_column(JSON, default=list) # List of topic strings
    cities: Mapped[str] = mapped_column(JSON, default=list) # List of city slugs
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
