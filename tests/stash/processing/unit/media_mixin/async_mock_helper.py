"""Helper module for async mocks in tests."""

from unittest.mock import AsyncMock, MagicMock


def async_return(value):
    """Create an awaitable that returns a value."""

    async def _async_return():
        return value

    return _async_return()


# Simple awaitable wrapper for any object's attributes
class AwaitableAttributesMock:
    """Mock for awaitable_attrs that returns awaitable properties."""

    def __init__(self, parent):
        """Initialize with parent object."""
        self._parent = parent
        self._attr_mocks = {}

    def __getattr__(self, name):
        """Return an awaitable that resolves to the parent's attribute."""
        # If we already have a mock for this attribute, return it
        if name in self._attr_mocks:
            return self._attr_mocks[name]

        # Get the attribute value from parent
        if hasattr(self._parent, name):
            value = getattr(self._parent, name)
        else:
            # Default to empty list for missing attributes
            value = []

        # Create and return an awaitable
        return async_return(value)

    def __setattr__(self, name, value):
        """Store the mock for future retrieval."""
        if name.startswith("_"):
            # For internal attributes, use normal behavior
            super().__setattr__(name, value)
        else:
            # Store in _attr_mocks for external access
            self._attr_mocks[name] = value


# Enhanced mock object that can be both awaited and accessed directly
class AccessibleAsyncMock(MagicMock):
    """Mock that can be both awaited and have properties accessed directly.

    This solves the problem of 'coroutine' object has no attribute issues.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make the __call__ method return an awaitable with proper attributes
        self.__call__ = AsyncMock(return_value=self)

        # Create awaitable_attrs that returns awaitable properties
        self._awaitable_attrs = AwaitableAttributesMock(self)

    def __getattribute__(self, name):
        """Override to handle the special 'awaitable_attrs' attribute."""
        if name == "awaitable_attrs":
            return object.__getattribute__(self, "_awaitable_attrs")
        return super().__getattribute__(name)

    def __await__(self):
        """Make the mock awaitable directly."""
        return async_return(self)().__await__()


# Create accessible async context manager
class AsyncContextManagerMock(MagicMock):
    """Mock for async context managers."""

    def __init__(self, *args, return_value=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


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


# Fix AsyncMock to be properly awaitable
def make_asyncmock_awaitable(mock_obj):
    """Make an AsyncMock directly awaitable by adding __await__ method."""
    if isinstance(mock_obj, AsyncMock) and not hasattr(mock_obj, "__await__"):
        mock_obj.__await__ = lambda: async_return(mock_obj.return_value)().__await__()

    return mock_obj


def make_awaitable_mock(obj):
    """Convert a MagicMock to one that properly handles awaitable attributes.

    This is useful for mocking objects that might be passed to functions that use
    `await obj.attr` syntax, ensuring that the attributes can be properly awaited
    and also accessed directly.

    Args:
        obj: MagicMock object to convert

    Returns:
        Modified MagicMock with awaitable attributes
    """
    # Make AsyncMock directly awaitable
    if isinstance(obj, AsyncMock):
        make_asyncmock_awaitable(obj)

    # Initialize awaitable_attrs if needed
    if hasattr(obj, "awaitable_attrs") and not isinstance(
        obj.awaitable_attrs, AwaitableAttributesMock
    ):
        # Get existing awaitable_attrs
        existing_attrs = obj.awaitable_attrs

        # Create new awaitable_attrs
        obj._awaitable_attrs = AwaitableAttributesMock(obj)

        # Transfer any explicitly set attributes
        for name in dir(existing_attrs):
            if not name.startswith("_") and not callable(
                getattr(existing_attrs, name, None)
            ):
                value = getattr(existing_attrs, name, None)
                if isinstance(value, AsyncMock):
                    make_asyncmock_awaitable(value)
                setattr(obj._awaitable_attrs, name, value)

    # Add awaitable_attrs if it doesn't exist
    elif not hasattr(obj, "awaitable_attrs"):
        obj._awaitable_attrs = AwaitableAttributesMock(obj)

        # Add property to redirect awaitable_attrs to _awaitable_attrs
        class AwaitableAttrsDescriptor:
            def __get__(self, instance, owner):
                if instance is None:
                    return None
                return instance._awaitable_attrs

        cls = obj.__class__
        if not hasattr(cls, "__awaitable_attrs_added"):
            setattr(cls, "awaitable_attrs", AwaitableAttrsDescriptor())
            setattr(cls, "__awaitable_attrs_added", True)

    return obj
