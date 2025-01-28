# Database Layer Improvements

## Current Issues

1. High Complexity in close_sync:
   ```python
   def close_sync(self) -> None:  # Complexity: 13
   ```
   - Complex thread-local cleanup
   - Mixed error handling
   - Multiple responsibilities

2. Async/Sync Inconsistencies:
   - Some methods have both versions
   - Others only have one version
   - Inconsistent error handling

3. Transaction Management:
   - Mixed context manager usage
   - Nested transaction issues
   - Savepoint handling

4. Resource Management:
   - Complex cleanup patterns
   - Thread-local and async resources
   - Lock management

## Improvement Plan

### 1. Resource Management Classes

```python
class ThreadLocalConnections:
    """Manage thread-local database connections."""
    def __init__(self):
        self._connections = local()
        self._lock = threading.Lock()

    async def cleanup(self) -> None:
        """Clean up all thread-local connections."""
        with self._lock:
            for thread_id in self._get_thread_ids():
                await self._cleanup_thread(thread_id)

    def _get_thread_ids(self) -> list[str]:
        """Get all thread IDs with connections."""
        return [tid for tid in dir(self._connections) if tid.isdigit()]

class AsyncConnections:
    """Manage async database connections."""
    def __init__(self):
        self._connections: dict[int, tuple[Any, int]] = {}
        self._lock = asyncio.Lock()

    async def cleanup(self) -> None:
        """Clean up all async connections."""
        async with self._lock:
            for task_id in list(self._connections):
                await self._cleanup_task(task_id)
```

### 2. Transaction Management

```python
class TransactionManager:
    """Manage database transactions."""
    def __init__(self, session: Session | AsyncSession):
        self.session = session
        self._savepoint_id = 0

    async def begin(self) -> AsyncContextManager:
        """Begin a new transaction or savepoint."""
        if self.session.in_transaction():
            return self.begin_nested()
        return self.session.begin()

    async def begin_nested(self) -> AsyncContextManager:
        """Begin a new savepoint."""
        self._savepoint_id += 1
        return self.session.begin_nested()
```

### 3. Connection Management

```python
class ConnectionManager:
    """Manage database connections."""
    def __init__(self):
        self.thread_connections = ThreadLocalConnections()
        self.async_connections = AsyncConnections()
        self._cleanup_lock = asyncio.Lock()

    async def cleanup(self) -> None:
        """Clean up all connections."""
        async with self._cleanup_lock:
            await asyncio.gather(
                self.thread_connections.cleanup(),
                self.async_connections.cleanup()
            )
```

### 4. Improved Database Class

```python
class Database:
    """Database management with proper async support."""
    def __init__(self, config: FanslyConfig):
        self.config = config
        self.connection_manager = ConnectionManager()
        self.transaction_manager = TransactionManager()
        self._setup_engines()
        self._setup_session_factories()

    async def cleanup(self) -> None:
        """Clean up all database resources."""
        try:
            async with asyncio.timeout(10):
                await self.connection_manager.cleanup()
                await self._cleanup_engines()
        except asyncio.TimeoutError:
            print_error("Timeout during database cleanup")
            self._force_cleanup()
```

## Implementation Steps

1. Resource Management:
   - Implement ThreadLocalConnections
   - Implement AsyncConnections
   - Add proper error handling
   - Add timeout support

2. Transaction Management:
   - Implement TransactionManager
   - Add savepoint support
   - Add nested transaction support
   - Add error handling

3. Connection Management:
   - Implement ConnectionManager
   - Add cleanup coordination
   - Add resource tracking
   - Add error recovery

4. Database Class:
   - Update initialization
   - Add new managers
   - Update session factories
   - Update cleanup methods

5. Testing:
   - Add unit tests for new classes
   - Add integration tests
   - Add stress tests
   - Add cleanup tests

## Implementation Strategy

1. Replace Classes:
   - Replace one class at a time
   - Add tests before replacing
   - Verify functionality

2. Update Callers:
   - Update each module immediately
   - Add tests for each module
   - Verify no regressions

3. Clean Up:
   - Remove old code
   - Clean up imports
   - Update type hints

4. Documentation:
   - Update docstrings
   - Add examples
   - Update README
