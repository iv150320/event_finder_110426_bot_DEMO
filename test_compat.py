#!/usr/bin/env python3
"""Test compatibility layer for old tests."""

import os
import sqlite3
from pathlib import Path


def setup_test_cache():
    """Setup test cache for backward compatibility with old tests."""
    # Create temporary cache file for tests
    test_cache = Path("/tmp/test_cache.db")
    if test_cache.exists():
        test_cache.unlink()

    # Create cache table
    conn = sqlite3.connect(str(test_cache))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, timestamp REAL, data_json TEXT)"
    )
    conn.commit()
    conn.close()

    # Set environment variable for tests
    os.environ["TEST_CACHE_FILE"] = str(test_cache)
    return test_cache


def cleanup_test_cache():
    """Cleanup test cache."""
    test_cache = Path("/tmp/test_cache.db")
    if test_cache.exists():
        test_cache.unlink()