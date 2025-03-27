"""Integration tests for database performance patterns.

Tests performance characteristics including:
- Bulk operations
- Query optimization
- Index usage
- Connection pooling
- Memory usage
"""

from __future__ import annotations

import gc
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from metadata import Account, Message, Post

if TYPE_CHECKING:
    from metadata.database import Database


def measure_time(func):
    """Decorator to measure execution time."""

    def wrapper(*args, **kwargs):
        gc.collect()  # Clean up before measurement
        start_time = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start_time
        print(f"{func.__name__} took {duration:.2f} seconds")
        return result

    return wrapper


async def create_bulk_data(
    session: AsyncSession, num_accounts: int = 10
) -> list[Account]:
    """Create bulk test data."""
    accounts = []
    for i in range(num_accounts):
        account = Account(
            id=i + 1,
            username=f"perf_user_{i}",
            createdAt=datetime.now(timezone.utc),
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
                createdAt=datetime.now(timezone.utc),
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
                createdAt=datetime.now(timezone.utc),
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


@measure_time
async def test_bulk_insert_performance(test_database):
    """Test bulk insert performance."""
    BATCH_SIZE = 1000
    NUM_BATCHES = 10

    async with test_database.async_session_scope() as session:
        account = Account(
            id=1,
            username="bulk_test_user",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account)
        await session.flush()

        for batch in range(NUM_BATCHES):
            posts = [
                Post(
                    id=batch * BATCH_SIZE + i + 1,
                    accountId=account.id,
                    content=f"Bulk test post {i}",
                    createdAt=datetime.now(timezone.utc),
                )
                for i in range(BATCH_SIZE)
            ]
            session.add_all(posts)  # Use add_all instead of bulk_save_objects for async
            await session.flush()

        # Verify results
        result = await session.execute(
            text("SELECT COUNT(*) FROM posts WHERE accountId = :account_id"),
            {"account_id": account.id},
        )
        post_count = result.scalar()
        assert post_count == BATCH_SIZE * NUM_BATCHES


@measure_time
async def test_query_optimization(test_database):
    """Test query optimization techniques."""
    async with test_database.async_session_scope() as session:
        # Test 1: Basic query
        start_time = time.time()
        result = await session.execute(text("SELECT COUNT(*) FROM posts"))
        posts_count = result.scalar()
        basic_time = time.time() - start_time
        print(f"Basic query time for {posts_count} posts: {basic_time:.2f}s")

        # Test 2: Optimized query with specific columns
        start_time = time.time()
        result = await session.execute(text("SELECT id, content FROM posts"))
        column_posts = result.fetchall()
        column_time = time.time() - start_time
        print(
            f"Column-specific query time ({len(column_posts)} posts): {column_time:.2f}s"
        )
        assert column_time < basic_time

        # Test 3: Query with joins
        start_time = time.time()
        result = await session.execute(
            text(
                """
                SELECT p.* FROM posts p
                JOIN accounts a ON p.accountId = a.id
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
                JOIN accounts a ON p.accountId = a.id
                WHERE p.id < 100
            """
            )
        )
        joined_posts = result.fetchall()
        join_time = time.time() - start_time
        print(f"Join query time ({len(joined_posts)} posts): {join_time:.2f}s")


@measure_time
async def test_index_performance(test_database):
    """Test index usage and performance."""
    async with test_database.async_session_scope() as session:
        # Test queries with and without indexes
        queries = [
            ("Index scan on primary key", "SELECT * FROM posts WHERE id = 1"),
            (
                "Index scan on foreign key",
                "SELECT * FROM posts WHERE accountId = 1",
            ),
            ("Full table scan", "SELECT * FROM posts WHERE content LIKE '%test%'"),
            (
                "Index scan with join",
                """
                SELECT p.* FROM posts p
                JOIN accounts a ON p.accountId = a.id
                WHERE a.username = 'perf_user_1'
                """,
            ),
        ]

        for description, query in queries:
            # Get query plan
            result = await session.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            plan = result.fetchall()
            plan_str = "\n".join(str(row) for row in plan)
            print(f"\n{description} plan:\n{plan_str}")

            # Execute query and measure time
            start_time = time.time()
            result = await session.execute(text(query))
            await result.fetchall()
            duration = time.time() - start_time
            print(f"{description} execution time: {duration:.2f}s")


@measure_time
async def test_connection_pool_performance(test_database):
    """Test connection pool performance."""
    NUM_OPERATIONS = 100  # Reduced from 1000 to avoid overloading

    async def perform_operation(session: AsyncSession) -> None:
        """Perform a simple database operation."""
        result = await session.execute(text("SELECT 1"))
        await result.fetchone()

    # Test 1: Sequential operations
    start_time = time.time()
    for _ in range(NUM_OPERATIONS):
        async with test_database.async_session_scope() as session:
            await perform_operation(session)
    sequential_time = time.time() - start_time
    print(f"Sequential operations time: {sequential_time:.2f}s")

    # Test 2: Parallel operations with session reuse
    import asyncio

    async def worker():
        async with test_database.async_session_scope() as session:
            try:
                await perform_operation(session)
            except Exception:
                await session.rollback()
                raise

    start_time = time.time()
    tasks = [worker() for _ in range(NUM_OPERATIONS)]
    await asyncio.gather(*tasks)
    parallel_time = time.time() - start_time
    print(f"Parallel operations time: {parallel_time:.2f}s")


@measure_time
async def test_memory_usage(test_database):
    """Test memory usage patterns."""
    import os

    import psutil

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
        chunk_size = 1000
        post_count = 0
        # Stream data in chunks using raw SQL
        result = await session.execute(
            text("SELECT * FROM posts"),
            execution_options={"stream_results": True, "max_row_buffer": chunk_size},
        )
        async for _ in result:
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


@measure_time
async def test_query_caching(test_database):
    """Test query caching patterns."""

    async def run_query(
        session: AsyncSession, account_id: int
    ) -> list[tuple[int, str]]:
        result = await session.execute(
            text("SELECT id, content FROM posts WHERE accountId = :account_id"),
            {"account_id": account_id},
        )
        return result.fetchall()

    async with test_database.async_session_scope() as session:
        # Create test data
        account = Account(
            id=1,
            username="test_user",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account)
        await session.flush()

        # Add some posts
        for i in range(10):
            post = Post(
                id=i + 1,
                accountId=account.id,
                content=f"Test post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(post)
        await session.commit()

        # Test 1: First query execution
        start_time = time.time()
        posts1 = await run_query(session, 1)
        first_time = time.time() - start_time
        print(f"First query time ({len(posts1)} posts): {first_time:.2f}s")

        # Test 2: Second query execution (should use statement cache)
        start_time = time.time()
        posts2 = await run_query(session, 1)
        second_time = time.time() - start_time
        print(f"Second query time ({len(posts2)} posts): {second_time:.2f}s")
        assert second_time < first_time
        assert posts1 == posts2  # Results should be identical

        # Test 3: Query with different parameter (should reuse template)
        start_time = time.time()
        posts3 = await run_query(session, 2)  # Different account ID
        third_time = time.time() - start_time
        print(f"Third query time ({len(posts3)} posts): {third_time:.2f}s")
        assert third_time < first_time
        # Results should be different since we used a different account
        assert posts3 != posts1
