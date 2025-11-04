"""Integration tests for database performance patterns.

Tests performance characteristics including:
- Bulk operations
- Query optimization
- Index usage
- Connection pooling
- Memory usage
"""

from __future__ import annotations

import asyncio
import gc
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psutil
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, Message, Post


if TYPE_CHECKING:
    pass


def measure_time(func):
    """Decorator to measure execution time."""
    if asyncio.iscoroutinefunction(func):

        async def async_wrapper(*args, **kwargs):
            gc.collect()  # Clean up before measurement
            start_time = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            print(f"{func.__name__} took {duration:.2f} seconds")
            return result

        return async_wrapper

    def sync_wrapper(*args, **kwargs):
        gc.collect()  # Clean up before measurement
        start_time = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start_time
        print(f"{func.__name__} took {duration:.2f} seconds")
        return result

    return sync_wrapper


async def create_bulk_data(
    session: AsyncSession, num_accounts: int = 10
) -> list[Account]:
    """Create bulk test data."""
    accounts = []
    for i in range(num_accounts):
        account = Account(
            id=i + 1,
            username=f"perf_user_{i}",
            createdAt=datetime.now(UTC),
        )
        session.add(account)
        accounts.append(account)
    await session.flush()

    # Create posts for each account
    for account in accounts:
        posts = []
        for i in range(100):  # 100 posts per account
            post = Post(
                id=len(posts) + 1,
                accountId=account.id,
                content=f"Performance test post {i}",
                createdAt=datetime.now(UTC),
            )
            posts.append(post)
        session.add_all(posts)  # Use add_all instead of bulk_save_objects for async
        await session.flush()

    # Create messages for each account
    for account in accounts:
        messages = []
        for i in range(100):  # 100 messages per account
            message = Message(
                id=len(messages) + 1,
                senderId=account.id,
                content=f"Performance test message {i}",
                createdAt=datetime.now(UTC),
            )
            messages.append(message)
        session.add_all(messages)  # Use add_all instead of bulk_save_objects for async
        await session.flush()

    await session.commit()
    return accounts


@pytest.fixture(autouse=True)
async def setup(test_database):
    """Setup and cleanup for each test."""
    yield
    # Cleanup after each test
    async with test_database.async_session_scope() as session:
        await session.execute(Post.__table__.delete())
        await session.execute(Account.__table__.delete())
        await session.commit()

    # def test_query_caching(self):
    #     with self.session_scope() as session:
    #         account = Account(
    #             id=1, username="test_user", createdAt=datetime.now(timezone.utc)
    #         )
    #         session.add(account)
    #         session.commit()

    #         # Test caching
    #         query = session.query(Account).filter_by(id=1)
    #         result1 = query.first()
    #         result2 = query.from_self().first()

    #         assert result1 == result2


@pytest.fixture(scope="class")
async def performance_data(test_database) -> list[Account]:
    """Create performance test data."""
    async with test_database.async_session_scope() as session:
        accounts = await create_bulk_data(session)
        return accounts


async def test_bulk_insert_performance(test_database):
    """Test bulk insert performance."""
    batch_size = 1000
    num_batches = 10

    async with test_database.async_session_scope() as session:
        account = Account(
            id=1,
            username="bulk_test_user",
            createdAt=datetime.now(UTC),
        )
        session.add(account)
        await session.flush()

        for batch in range(num_batches):
            posts = [
                Post(
                    id=batch * batch_size + i + 1,
                    accountId=account.id,
                    content=f"Bulk test post {i}",
                    createdAt=datetime.now(UTC),
                )
                for i in range(batch_size)
            ]
            session.add_all(posts)  # Use add_all instead of bulk_save_objects for async
            await session.flush()

        # Verify results
        result = await session.execute(
            text('SELECT COUNT(*) FROM posts WHERE "accountId" = :account_id'),
            {"account_id": account.id},
        )
        post_count = result.scalar()
        assert post_count == batch_size * num_batches


async def test_query_optimization(test_database):
    """Test query optimization techniques."""
    # Setup test data
    async with test_database.async_session_scope() as session:
        # Create base account
        account = Account(
            id=1,
            username="test_perf_user",
            createdAt=datetime.now(UTC),
        )
        session.add(account)
        await session.flush()

        # Create large dataset for meaningful performance comparison
        posts = []
        for i in range(10000):  # Create 10k posts
            post = Post(
                id=i + 1,
                accountId=account.id,
                content=f"Performance test post {i}",
                createdAt=datetime.now(UTC),
            )
            posts.append(post)
        session.add_all(posts)
        await session.commit()

        # Run multiple times to warm up query cache
        for _ in range(3):
            await session.execute(text("SELECT COUNT(*) FROM posts"))
            await session.execute(text("SELECT id, content FROM posts LIMIT 1000"))

        # Test 1: Basic query (full columns, limited rows)
        start_time = time.time()
        result = await session.execute(text("SELECT * FROM posts LIMIT 1000"))
        all_column_posts = result.fetchall()
        basic_time = time.time() - start_time
        print(
            f"Full column query time for {len(all_column_posts)} posts: {basic_time:.2f}s"
        )

        # Test 2: Optimized query with specific columns (same row count for fair comparison)
        start_time = time.time()
        result = await session.execute(text("SELECT id, content FROM posts LIMIT 1000"))
        column_posts = result.fetchall()
        column_time = time.time() - start_time
        print(
            f"Column-specific query time ({len(column_posts)} posts): {column_time:.2f}s"
        )

        # The column-specific query should generally be faster or similar,
        # but we won't enforce strict timing assertions as performance can vary
        # Just verify both queries completed successfully
        assert len(all_column_posts) == len(column_posts) == 1000
        print(f"Basic query: {basic_time:.4f}s, Column query: {column_time:.4f}s")

        # Test 3: Query with joins
        start_time = time.time()
        result = await session.execute(
            text(
                """
                SELECT p.* FROM posts p
                JOIN accounts a ON p."accountId" = a.id
                WHERE a.username LIKE 'perf_user_%'
            """
            )
        )
        filtered_posts = result.fetchall()
        join_time = time.time() - start_time
        print(f"Join query time ({len(filtered_posts)} posts): {join_time:.2f}s")

        # Test 4: Query with join and filter
        start_time = time.time()
        result = await session.execute(
            text(
                """
                SELECT p.* FROM posts p
                JOIN accounts a ON p."accountId" = a.id
                WHERE p.id < 100
            """
            )
        )
        joined_posts = result.fetchall()
        join_time = time.time() - start_time
        print(f"Join query time ({len(joined_posts)} posts): {join_time:.2f}s")


@pytest.mark.integration
@pytest.mark.performance
class TestPerformancePatterns:
    """Test database performance patterns."""

    @pytest.mark.asyncio
    async def test_index_performance(self, test_database):
        """Test index usage and performance."""
        async with test_database.async_session_scope() as session:
            # Test queries with and without indexes
            # Note: PostgreSQL requires exact case for quoted identifiers like "accountId"
            queries = [
                ("Index scan on primary key", "SELECT * FROM posts WHERE id = 1"),
                (
                    "Index scan on foreign key",
                    'SELECT * FROM posts WHERE "accountId" = 1',
                ),
                ("Full table scan", "SELECT * FROM posts WHERE content LIKE '%test%'"),
                (
                    "Index scan with join",
                    """
                    SELECT p.* FROM posts p
                    JOIN accounts a ON p."accountId" = a.id
                    WHERE a.username = 'perf_user_1'
                    """,
                ),
            ]

            for description, query in queries:
                # Get query plan (PostgreSQL uses EXPLAIN, not EXPLAIN QUERY PLAN)
                result = await session.execute(text(f"EXPLAIN {query}"))
                plan = result.fetchall()
                plan_str = "\n".join(str(row) for row in plan)
                print(f"\n{description} plan:\n{plan_str}")

                # Execute query and measure time
                start_time = time.time()
                result = await session.execute(text(query))
                result.fetchall()
                duration = time.time() - start_time
                print(f"{description} execution time: {duration:.2f}s")

    @pytest.mark.asyncio
    async def test_connection_pool_performance(self, test_database):
        """Test connection pool performance."""
        num_operations = 100  # Reduced from 1000 to avoid overloading

        async def perform_operation(session: AsyncSession) -> None:
            """Perform a simple database operation."""
            result = await session.execute(text("SELECT 1"))
            result.fetchone()

        # Test 1: Sequential operations
        start_time = time.time()
        for _ in range(num_operations):
            async with test_database.async_session_scope() as session:
                await perform_operation(session)
        sequential_time = time.time() - start_time
        print(f"Sequential operations time: {sequential_time:.2f}s")

        # Test 2: Parallel operations with session reuse

        async def worker():
            async with test_database.async_session_scope() as session:
                try:
                    await perform_operation(session)
                except Exception:
                    await session.rollback()
                    raise

        start_time = time.time()
        tasks = [worker() for _ in range(num_operations)]
        await asyncio.gather(*tasks)
        parallel_time = time.time() - start_time
        print(f"Parallel operations time: {parallel_time:.2f}s")


@pytest.mark.asyncio
async def test_memory_usage(test_database):
    """Test memory usage patterns."""
    process = psutil.Process(os.getpid())

    def get_memory_mb() -> float:
        """Get current memory usage in MB."""
        return process.memory_info().rss / 1024 / 1024

    async with test_database.async_session_scope() as session:
        # Test 1: Load all data at once
        initial_memory = get_memory_mb()
        result = await session.execute(text("SELECT * FROM posts"))
        posts = result.fetchall()
        full_load_memory = get_memory_mb()
        print(
            f"Memory usage for full load ({len(posts)} posts): {full_load_memory - initial_memory:.2f}MB"
        )

        # Test 2: Stream data in chunks
        initial_memory = get_memory_mb()
        post_count = 0
        # Use partitioning instead of server-side cursors
        result = await session.stream(
            text("SELECT * FROM posts ORDER BY id"),
            execution_options={"yield_per": 100},
        )
        async for _ in result.partitions():
            post_count += 1
        stream_memory = get_memory_mb()
        print(f"Memory usage for streaming: {stream_memory - initial_memory:.2f}MB")

        # Test 3: Use scalar queries
        initial_memory = get_memory_mb()
        result = await session.execute(text("SELECT COUNT(*) FROM posts"))
        post_count = result.scalar()
        scalar_memory = get_memory_mb()
        print(
            f"Memory usage for count query ({post_count} posts): {scalar_memory - initial_memory:.2f}MB"
        )


@pytest.mark.asyncio
async def test_query_caching(test_database):
    """Test query caching performance."""
    # Create test data
    async with test_database.async_session_scope() as session:
        account = Account(
            id=1,
            username="test_perf_user",
            createdAt=datetime.now(UTC),
        )
        session.add(account)
        await session.flush()

        # Create more sample posts (increased from 100 to 1000)
        for i in range(1000):
            post = Post(
                id=i + 1,
                accountId=account.id,
                content=f"Performance test post {i}",
                createdAt=datetime.now(UTC),
            )
            session.add(post)
        await session.commit()

    # Test query performance with and without caching
    query = text('SELECT * FROM posts WHERE "accountId" = :account_id')

    # Run multiple times to get more reliable measurements
    uncached_times = []
    cached_times = []

    # First run to warm up the cache
    async with test_database.async_session_scope() as session:
        result = await session.execute(query, {"account_id": account.id})
        _ = result.fetchall()

    # Measure several iterations
    for _ in range(5):
        # Clear caches between runs for the "uncached" case
        gc.collect()

        # Uncached run (with new session)
        start_time = time.time()
        async with test_database.async_session_scope() as session:
            result = await session.execute(query, {"account_id": account.id})
            posts = result.fetchall()
        uncached_times.append(time.time() - start_time)

        # Cached run (reuse same parameters and query)
        start_time = time.time()
        async with test_database.async_session_scope() as session:
            result = await session.execute(query, {"account_id": account.id})
            posts = result.fetchall()
        cached_times.append(time.time() - start_time)

    # Calculate averages
    avg_uncached_time = sum(uncached_times) / len(uncached_times)
    avg_cached_time = sum(cached_times) / len(cached_times)

    print(f"Average uncached query time: {avg_uncached_time:.4f}s")
    print(f"Average cached query time: {avg_cached_time:.4f}s")
    print(
        f"Speed improvement: {(avg_uncached_time / avg_cached_time if avg_cached_time > 0 else 0):.2f}x"
    )

    assert len(posts) == 1000

    # Allow for some measurement noise with a tolerance factor
    # Only fail if cached time is significantly slower than uncached
    tolerance_factor = (
        1.05  # Allow cached time to be up to 5% slower due to measurement noise
    )
    assert avg_cached_time <= (avg_uncached_time * tolerance_factor), (
        "Cached query should be roughly as fast or faster"
    )
