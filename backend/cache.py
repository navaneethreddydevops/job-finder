"""Caching layer for job results and frequently-accessed data."""

import functools
import hashlib
import json
from typing import Any, Callable, Optional
from datetime import datetime, timedelta

# In-memory cache as fallback (when Redis is unavailable)
_memory_cache = {}
_cache_timestamps = {}

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


class CacheConfig:
    """Cache configuration."""
    DEFAULT_TTL = 3600  # 1 hour
    JOB_RESULTS_TTL = 86400  # 24 hours
    SEARCH_RESULTS_TTL = 3600  # 1 hour
    USER_PREFS_TTL = 1800  # 30 minutes


def get_redis_client():
    """Get Redis client. Returns None if Redis is unavailable."""
    if not REDIS_AVAILABLE:
        return None

    try:
        client = redis.Redis(
            host='localhost',
            port=6379,
            db=0,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        client.ping()
        return client
    except Exception:
        return None


def cache_key(*args, **kwargs) -> str:
    """Generate a cache key from function arguments."""
    key_parts = [str(arg) for arg in args]
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_str = "|".join(key_parts)
    return hashlib.md5(key_str.encode()).hexdigest()


def cache(ttl: int = CacheConfig.DEFAULT_TTL):
    """
    Decorator for caching function results.
    Usage: @cache(ttl=3600)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            key = cache_key(func.__name__, *args, **kwargs)

            # Try Redis first
            redis_client = get_redis_client()
            if redis_client:
                try:
                    result = redis_client.get(key)
                    if result:
                        return json.loads(result)
                except Exception:
                    pass

            # Fall back to in-memory cache
            if key in _memory_cache:
                if datetime.utcnow() < _cache_timestamps[key]:
                    return _memory_cache[key]
                else:
                    del _memory_cache[key]
                    del _cache_timestamps[key]

            # Cache miss - execute function
            result = func(*args, **kwargs)

            # Store in both caches
            if redis_client:
                try:
                    redis_client.setex(
                        key,
                        ttl,
                        json.dumps(result, default=str),
                    )
                except Exception:
                    pass

            # Store in memory cache as fallback
            _memory_cache[key] = result
            _cache_timestamps[key] = datetime.utcnow() + timedelta(seconds=ttl)

            return result

        return wrapper

    return decorator


def invalidate_cache(pattern: str = "*"):
    """
    Invalidate cached entries matching pattern.
    Pattern: '*' invalidates all, 'user:123*' invalidates all for user 123
    """
    redis_client = get_redis_client()

    if redis_client:
        try:
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
        except Exception:
            pass

    # Also clear matching memory cache
    if pattern == "*":
        _memory_cache.clear()
        _cache_timestamps.clear()
    else:
        # Simple pattern matching for memory cache
        import re

        regex = pattern.replace("*", ".*")
        to_delete = [k for k in _memory_cache if re.match(regex, k)]
        for k in to_delete:
            del _memory_cache[k]
            if k in _cache_timestamps:
                del _cache_timestamps[k]


def set_cache(key: str, value: Any, ttl: int = CacheConfig.DEFAULT_TTL):
    """Manually set a cache value."""
    redis_client = get_redis_client()

    if redis_client:
        try:
            redis_client.setex(
                key,
                ttl,
                json.dumps(value, default=str),
            )
        except Exception:
            pass

    _memory_cache[key] = value
    _cache_timestamps[key] = datetime.utcnow() + timedelta(seconds=ttl)


def get_cache(key: str) -> Optional[Any]:
    """Retrieve a cached value."""
    redis_client = get_redis_client()

    if redis_client:
        try:
            result = redis_client.get(key)
            if result:
                return json.loads(result)
        except Exception:
            pass

    if key in _memory_cache:
        if datetime.utcnow() < _cache_timestamps[key]:
            return _memory_cache[key]
        else:
            del _memory_cache[key]
            del _cache_timestamps[key]

    return None


def clear_user_cache(user_id: str):
    """Clear all cache entries for a specific user."""
    invalidate_cache(f"*user_id={user_id}*")


# Cache decorators for specific use cases
def cache_jobs(ttl: int = CacheConfig.JOB_RESULTS_TTL):
    """Cache job search results."""
    return cache(ttl=ttl)


def cache_search(ttl: int = CacheConfig.SEARCH_RESULTS_TTL):
    """Cache search results."""
    return cache(ttl=ttl)


def cache_user_prefs(ttl: int = CacheConfig.USER_PREFS_TTL):
    """Cache user preferences."""
    return cache(ttl=ttl)
