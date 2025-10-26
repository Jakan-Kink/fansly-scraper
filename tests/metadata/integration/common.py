def measure_time(func):
    """Decorator to measure execution time for sync and async functions."""
    import asyncio
    import gc
    import inspect
    import time
    from functools import wraps

    if asyncio.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            gc.collect()
            start_time = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            print(f"{func.__name__} took {duration:.2f} seconds")
            return result

        async_wrapper.__signature__ = inspect.signature(func)
        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            gc.collect()
            start_time = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            print(f"{func.__name__} took {duration:.2f} seconds")
            return result

        return sync_wrapper
