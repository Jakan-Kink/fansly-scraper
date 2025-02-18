import functools
import json
import re
import string

from .logging import debug_print


def normalize_str(string_in):
    # remove punctuation
    punctuation = re.compile(f"[{string.punctuation}]")
    string_in = re.sub(punctuation, " ", string_in)

    # normalize whitespace
    whitespace = re.compile(f"[{string.whitespace}]+")
    string_in = re.sub(whitespace, " ", string_in)

    # remove leading and trailing whitespace
    string_in = string_in.strip(string.whitespace)

    return string_in


def str_compare(s1, s2, ignore_case=True):
    s1 = normalize_str(s1)
    s2 = normalize_str(s2)
    if ignore_case:
        s1 = s1.lower()
        s2 = s2.lower()
    return s1 == s2


def async_lru_cache(maxsize=128, exclude_arg_indices=None):
    """Decorator to add LRU caching to an async function.

    Args:
        maxsize: Maximum size of the cache. Once this size is reached,
                the least recently used items will be evicted.

    Returns:
        Decorated async function with caching.

    The cache key is based on the function arguments. For mutable arguments
    like dictionaries, they are converted to JSON strings with sorted keys
    to ensure consistent cache keys regardless of dict key order.

    The decorated function gets two additional methods:
    - cache_clear(): Clear the cache
    - cache_info(): Get info about cache size and capacity
    """

    def decorator(func):
        # Create cache
        cache = {}

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Create cache key from args and kwargs
            # Convert any dicts to sorted JSON for consistent keys
            def make_key_part(arg):
                if isinstance(arg, dict):
                    return json.dumps(arg, sort_keys=True)
                if isinstance(arg, (list, tuple)):
                    return tuple(make_key_part(x) for x in arg)
                return arg

            # Filter out excluded args
            excluded = exclude_arg_indices or []
            filtered_args = [arg for i, arg in enumerate(args) if i not in excluded]

            key = (
                tuple(make_key_part(arg) for arg in filtered_args),
                tuple(sorted((k, make_key_part(v)) for k, v in kwargs.items())),
            )

            # Check cache
            if key in cache:
                debug_print(
                    {
                        "method": "async_lru_cache",
                        "status": "cache_hit",
                        "func": func.__name__,
                        "args": args,
                        "kwargs": kwargs,
                    }
                )
                return cache[key]

            # Call function and cache result
            result = await func(*args, **kwargs)
            cache[key] = result

            # Maintain cache size
            if len(cache) > maxsize:
                # Remove least recently used item
                cache.pop(next(iter(cache)))

            debug_print(
                {
                    "method": "async_lru_cache",
                    "status": "cache_miss",
                    "func": func.__name__,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            return result

        # Add cache management methods
        wrapper.cache_clear = cache.clear
        wrapper.cache_info = lambda: {"maxsize": maxsize, "currsize": len(cache)}

        return wrapper

    return decorator
