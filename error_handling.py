#!/usr/bin/env python3
"""Improved error handling utilities for Event Finder."""

import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')

def with_retry(max_retries: int = 3, delay: float = 1.0,
               exceptions: tuple = (Exception,),
               swallow: bool = True) -> Callable:
    """Decorator for retrying sync function calls with exponential backoff.

    Args:
        swallow: If True (default), returns None after all retries exhausted.
                 If False, re-raises the last exception.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Optional[T]:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_retries - 1:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")
                        if not swallow:
                            raise
                        return None

                    sleep_time = delay * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    time.sleep(sleep_time)
            return None
        return wrapper
    return decorator


def async_with_retry(max_retries: int = 3, delay: float = 1.0,
                     exceptions: tuple = (Exception,),
                     swallow: bool = True) -> Callable:
    """Async-декоратор для retry с exponential backoff (asyncio.sleep вместо time.sleep).

    Args:
        swallow: If True (default), returns None after all retries exhausted.
                 If False, re-raises the last exception.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Optional[T]:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_retries - 1:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")
                        if not swallow:
                            raise
                        return None

                    sleep_time = delay * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    await asyncio.sleep(sleep_time)
            return None
        return wrapper
    return decorator


def safe_execute(func: Callable[..., T], *args, **kwargs) -> Optional[T]:
    """Safely execute a function and return None on any exception."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"Safe execute failed for {func.__name__}: {e}")
        return None


def log_errors(logger: logging.Logger = logger):
    """Decorator to log errors without raising."""
    def decorator(func: Callable[..., T]) -> Callable[..., Optional[T]]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Optional[T]:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                return None
        return wrapper
    return decorator


def specific_error_handler(*exception_types):
    """Decorator for handling specific exception types."""
    def decorator(func: Callable[..., T]) -> Callable[..., Optional[T]]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Optional[T]:
            try:
                return func(*args, **kwargs)
            except exception_types as e:
                logger.warning(f"Expected error in {func.__name__}: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {e}")
                raise
        return wrapper
    return decorator