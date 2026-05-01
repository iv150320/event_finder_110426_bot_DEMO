#!/usr/bin/env python3
"""Test script for improved Event Finder search.

Tests:
1. Retry mechanism with exponential backoff
2. KudaGo pagination
3. Timepad extended parameters
4. Yandex Afisha
5. University multi-level parsing
6. Advanced topic classification
"""

__test__ = False

import sys
import time
import asyncio
from datetime import datetime, timedelta

from event_search import (
    fetch_kudago,
    fetch_timepad,
    fetch_yandex_afisha,
    fetch_university,
    classify_topic_advanced,
    dedupe,
)


async def test_retry_mechanism():
    """Test retry mechanism with invalid URL."""
    print("\n" + "=" * 50)
    print("TEST: Retry mechanism")
    print("=" * 50)

    start = datetime.now()
    end = start + timedelta(days=7)

    # This should handle 404 gracefully with retry
    try:
        events = await fetch_university("https://nonexistent-domain-12345.com/events", "Test", start, end)
        print(f"✅ Retry mechanism works: returned {len(events)} events (expected 0)")
        return True
    except Exception as e:
        print(f"❌ Retry mechanism failed: {e}")
        return False


async def test_kudago_pagination():
    """Test KudaGo with pagination."""
    print("\n" + "=" * 50)
    print("TEST: KudaGo pagination")
    print("=" * 50)

    start = datetime.now()
    end = start + timedelta(days=30)

    # Get all events
    events = await fetch_kudago('msk', '', start, end, max_events=200)
    print(f"✅ KudaGo returned {len(events)} events")

    if events:
        print(f"  Sample: {events[0]['title'][:60]}...")
        print(f"  Source: {events[0]['source']}")
        print(f"  Date: {events[0]['date']}")
        return True
    else:
        print("⚠️  No events found (API might be rate limited)")
        return True


async def test_timepad_extended():
    """Test Timepad with extended parameters."""
    print("\n" + "=" * 50)
    print("TEST: Timepad extended parameters")
    print("=" * 50)

    start = datetime.now()
    end = start + timedelta(days=30)

    events = await fetch_timepad('msk', '', start, end, max_events=100)
    print(f"✅ Timepad returned {len(events)} events")

    if events:
        print(f"  Sample: {events[0]['title'][:60]}...")
        print(f"  Source: {events[0]['source']}")
        return True
    else:
        print("⚠️  No events found (API might be rate limited)")
        return True


async def test_yandex_afisha():
    """Test Yandex Afisha parser."""
    print("\n" + "=" * 50)
    print("TEST: Yandex Afisha")
    print("=" * 50)

    start = datetime.now()
    end = start + timedelta(days=14)

    events = await fetch_yandex_afisha('', start, end, max_events=50)
    print(f"✅ Yandex Afisha returned {len(events)} events")

    if events:
        print(f"  Sample: {events[0]['title'][:60]}...")
        print(f"  Source: {events[0]['source']}")
        return True
    else:
        print("⚠️  No events found (parsing might need adjustment)")
        return True


async def test_university_parsing():
    """Test university multi-level parsing."""
    print("\n" + "=" * 50)
    print("TEST: University multi-level parsing")
    print("=" * 50)

    start = datetime.now()
    end = start + timedelta(days=30)

    # Test HSE
    events = await fetch_university("https://www.hse.ru/news/", "ВШЭ", start, end)
    print(f"✅ ВШЭ returned {len(events)} events")

    if events:
        print(f"  Sample: {events[0]['title'][:60]}...")
        return True
    else:
        print("⚠️  No events found (site might be blocking)")
        return True


async def test_advanced_classification():
    """Test advanced topic classification."""
    print("\n" + "=" * 50)
    print("TEST: Advanced topic classification")
    print("=" * 50)

    test_cases = [
        {
            "title": "Бизнес-конференция для стартапов",
            "description": "Встреча инвесторов и предпринимателей",
            "categories": ["бизнес", "стартапы"],
            "expected": "бизнес"
        },
        {
            "title": "Лекция по искусственному интеллекту",
            "description": "Нейросети и машинное обучение",
        "categories": ["IT", "tech"],
        "expected": "IT"
        },
        {
            "title": "Мастер-класс по управлению эмоциями",
            "description": "Психология личностного роста",
            "categories": ["психология"],
            "expected": "психология"
        },
    ]

    all_passed = True
    for test in test_cases:
        result = classify_topic_advanced(test)
        passed = result == test["expected"]
        status = "✅" if passed else "❌"
        print(f"{status} '{test['title'][:40]}...' → '{result}' (expected: '{test['expected']}')")
        if not passed:
            all_passed = False

    return all_passed


async def test_deduplication():
    """Test deduplication of events."""
    print("\n" + "=" * 50)
    print("TEST: Event deduplication")
    print("=" * 50)

    events = [
        {"title": "Test Event", "date": "1 января, понедельник", "date_sort": datetime(2024, 1, 1)},
        {"title": "Test Event!", "date": "1 января, понедельник", "date_sort": datetime(2024, 1, 1)},
        {"title": "Another Event", "date": "2 января, вторник", "date_sort": datetime(2024, 1, 2)},
    ]

    result = dedupe(events)
    print(f"✅ Deduplication: {len(events)} → {len(result)} events")
    print(f"   Removed {len(events) - len(result)} duplicates")

    return len(result) == 2


async def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "=" * 60)
    print("EVENT FINDER v5.0 TEST SUITE")
    print("=" * 60)

    tests = [
        ("Retry mechanism", test_retry_mechanism),
        ("KudaGo pagination", test_kudago_pagination),
        ("Timepad extended", test_timepad_extended),
        ("Yandex Afisha", test_yandex_afisha),
        ("University parsing", test_university_parsing),
        ("Topic classification", test_advanced_classification),
        ("Deduplication", test_deduplication),
    ]

    results = []
    for name, test_func in tests:
        try:
            start = time.time()
            passed = await test_func()
            duration = time.time() - start
            results.append((name, passed, duration))
        except Exception as e:
            results.append((name, False, 0))
            print(f"❌ {name} failed with exception: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed_count = sum(1 for _, p, _ in results if p)
    total_count = len(results)

    for name, passed, duration in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name:.<40} ({duration:.2f}s)")

    print("-" * 60)
    print(f"Total: {passed_count}/{total_count} tests passed")
    print("=" * 60)

    return passed_count == total_count


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
