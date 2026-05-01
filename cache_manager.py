#!/usr/bin/env python3
"""Improved cache management with connection pooling and better key generation."""

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from queue import Queue

import logging

logger = logging.getLogger(__name__)


class ConnectionPool:
    """SQLite connection pool for thread-safe database access."""

    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = db_path
        self.max_connections = max_connections
        self.pool = Queue(max_connections)
        self._lock = threading.Lock()
        self._initialize_pool()

    def _initialize_pool(self):
        """Initialize the connection pool."""
        with self._lock:
            for _ in range(self.max_connections):
                conn = self._create_connection()
                self.pool.put(conn)

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, timestamp REAL, data_json TEXT)"
        )
        conn.commit()
        return conn

    def get_connection(self) -> sqlite3.Connection:
        """Get a connection from the pool."""
        try:
            return self.pool.get(block=True, timeout=5.0)
        except Exception as e:
            logger.warning(f"Connection pool timeout: {e}")
            return self._create_connection()

    def return_connection(self, conn: sqlite3.Connection):
        """Return a connection to the pool."""
        try:
            self.pool.put(conn, block=False)
        except Exception:
            conn.close()  # Pool is full, close connection


class CacheManager:
    """Improved cache management with better key generation and error handling."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(Path(__file__).parent / "data" / "cache.db")
        self.pool = ConnectionPool(self.db_path)
        self.ttl = 1800  # 30 minutes

    def get(self, key: str) -> Optional[Any]:
        """Get cached data with proper error handling."""
        conn = self.pool.get_connection()
        try:
            row = conn.execute(
                "SELECT timestamp, data_json FROM cache WHERE key = ?", (key,)
            ).fetchone()

            if row and (time.time() - row[0]) < self.ttl:
                data = json.loads(row[1])
                return self._deserialize_events(data) if isinstance(data, list) else data
            return None

        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.warning(f"Cache get error for key {key}: {e}")
            return None
        finally:
            self.pool.return_connection(conn)

    def set(self, key: str, data: Any):
        """Set cached data with proper error handling."""
        conn = self.pool.get_connection()
        try:
            serialized = self._serialize_events(data) if isinstance(data, list) else data

            conn.execute(
                "INSERT OR REPLACE INTO cache (key, timestamp, data_json) VALUES (?, ?, ?)",
                (key, time.time(), json.dumps(serialized, ensure_ascii=False)),
            )
            conn.commit()

        except (sqlite3.Error, OSError) as e:
            logger.warning(f"Cache set error for key {key}: {e}")
        finally:
            self.pool.return_connection(conn)

    def _serialize_events(self, events: list[dict]) -> list[dict]:
        """Convert datetime objects to ISO strings for JSON cache."""
        result = []
        for e in events:
            entry = dict(e)
            if "date_sort" in entry and isinstance(entry["date_sort"], datetime):
                entry["date_sort"] = entry["date_sort"].isoformat()
            if "date" in entry and isinstance(entry["date"], datetime):
                entry["date"] = entry["date"].isoformat()
            result.append(entry)
        return result

    def _deserialize_events(self, events: list[dict]) -> list[dict]:
        """Convert ISO strings back to datetime objects."""
        result = []
        for e in events:
            entry = dict(e)
            if "date_sort" in entry and isinstance(entry["date_sort"], str):
                try:
                    entry["date_sort"] = datetime.fromisoformat(entry["date_sort"])
                except ValueError:
                    pass
            if "date" in entry and isinstance(entry["date"], str):
                try:
                    entry["date"] = datetime.fromisoformat(entry["date"])
                except ValueError:
                    pass
            result.append(entry)
        return result

    def generate_key(self, source: str, **params) -> str:
        """Generate consistent cache key from parameters."""
        param_str = "_".join(f"{k}_{v}" for k, v in sorted(params.items()))
        return f"{source}_{param_str}"


# Global cache instance
cache_manager = CacheManager()