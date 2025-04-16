"""Helper module for async mocks in tests."""

from unittest.mock import AsyncMock


def async_return(value):
    """Create an awaitable that returns a value."""

    async def _async_return():
        return value

    return _async_return()


# Improved AsyncMock with proper __await__
class AwaitableAsyncMock(AsyncMock):
    """AsyncMock with __await__ support for full await compatibility."""

    def __init__(self, *args, return_value=None, **kwargs):
        """Initialize with return value."""
        super().__init__(*args, **kwargs)
        self._return_value = return_value

    def __await__(self):
        """Make the mock awaitable directly."""

        async def _async_return():
            return self._return_value

        return _async_return().__await__()


# Apply better AsyncMock
def patch_async_methods(mixin):
    """Patch async methods in a mixin to make them properly awaitable."""
    # Make find_existing_performer properly awaitable
    if hasattr(mixin, "_find_existing_performer"):
        orig_method = mixin._find_existing_performer

        async def awaitable_find_performer(*args, **kwargs):
            return orig_method.return_value

        mixin._find_existing_performer = awaitable_find_performer

    # Make find_existing_studio properly awaitable
    if hasattr(mixin, "_find_existing_studio"):
        orig_method = mixin._find_existing_studio

        async def awaitable_find_studio(*args, **kwargs):
            return orig_method.return_value

        mixin._find_existing_studio = awaitable_find_studio

    # Make process_hashtags_to_tags properly awaitable
    if hasattr(mixin, "_process_hashtags_to_tags"):
        orig_method = mixin._process_hashtags_to_tags

        async def awaitable_process_tags(*args, **kwargs):
            return orig_method.return_value

        mixin._process_hashtags_to_tags = awaitable_process_tags

    # Make add_preview_tag properly awaitable
    if hasattr(mixin, "_add_preview_tag"):
        orig_method = mixin._add_preview_tag

        async def awaitable_add_tag(*args, **kwargs):
            orig_method(*args, **kwargs)
            return None

        mixin._add_preview_tag = awaitable_add_tag

    # Make update_account_stash_id properly awaitable
    if hasattr(mixin, "_update_account_stash_id"):
        orig_method = mixin._update_account_stash_id

        async def awaitable_update_id(*args, **kwargs):
            orig_method(*args, **kwargs)
            return None

        mixin._update_account_stash_id = awaitable_update_id

    return mixin
