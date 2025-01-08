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
from sqlalchemy.orm import Session

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


def create_bulk_data(session: Session, num_accounts: int = 10) -> list[Account]:
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
    session.flush()

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
        session.bulk_save_objects(posts)

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
        session.bulk_save_objects(messages)

    session.commit()
    return accounts


class TestPerformancePatterns:
    """Test suite for database performance patterns."""

    @pytest.fixture(autouse=True)
    def setup(self, test_database):
        self.database = test_database
        yield
        # Cleanup after each test
        with self.session_scope() as session:
            session.query(Post).delete()
            session.query(Account).delete()
            session.commit()

    @contextmanager
    def session_scope(self):
        """Provide a transactional scope around a series of operations."""
        with self.database.get_sync_session() as session:
            yield session

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
    def performance_data(self, database: Database) -> list[Account]:
        """Create performance test data."""
        with database.get_sync_session() as session:
            accounts = create_bulk_data(session)
            return accounts

    @measure_time
    def test_bulk_insert_performance(self):
        BATCH_SIZE = 1000
        NUM_BATCHES = 10

        with self.session_scope() as session:
            account = Account(
                id=1,
                username="bulk_test_user",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(account)
            session.flush()

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
                session.bulk_save_objects(posts)
                session.flush()

            # Verify results
            post_count = session.query(Post).filter_by(accountId=account.id).count()
            assert post_count == BATCH_SIZE * NUM_BATCHES

    @measure_time
    def test_query_optimization(self):
        """Test query optimization techniques."""
        with self.database.session_scope() as session:
            # Test 1: Basic query
            start_time = time.time()
            posts_count = session.query(Post).count()
            basic_time = time.time() - start_time
            print(f"Basic query time for {posts_count} posts: {basic_time:.2f}s")

            # Test 2: Optimized query with specific columns
            start_time = time.time()
            column_posts = session.query(Post.id, Post.content).all()
            column_time = time.time() - start_time
            print(
                f"Column-specific query time ({len(column_posts)} posts): {column_time:.2f}s"
            )
            assert column_time < basic_time

            # Test 3: Query with joins
            start_time = time.time()
            filtered_posts = (
                session.query(Post)
                .join(Account)
                .filter(Account.username.like("perf_user_%"))
                .all()
            )
            join_time = time.time() - start_time
            print(f"Join query time ({len(filtered_posts)} posts): {join_time:.2f}s")

            # Test 4: Query with join
            start_time = time.time()
            joined_posts = session.query(Post).join(Account).filter(Post.id < 100).all()
            join_time = time.time() - start_time
            print(f"Join query time ({len(joined_posts)} posts): {join_time:.2f}s")

    @measure_time
    def test_index_performance(self):
        """Test index usage and performance."""
        with self.database.session_scope() as session:
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
                plan = session.execute(text(f"EXPLAIN QUERY PLAN {query}")).fetchall()
                plan_str = "\n".join(str(row) for row in plan)
                print(f"\n{description} plan:\n{plan_str}")

                # Execute query and measure time
                start_time = time.time()
                session.execute(text(query)).fetchall()
                duration = time.time() - start_time
                print(f"{description} execution time: {duration:.2f}s")

    @measure_time
    def test_connection_pool_performance(self):
        """Test connection pool performance."""
        NUM_OPERATIONS = 1000

        def perform_operation(session: Session) -> None:
            """Perform a simple database operation."""
            session.execute(text("SELECT 1")).fetchone()

        # Test 1: Sequential operations
        start_time = time.time()
        for _ in range(NUM_OPERATIONS):
            with self.database.get_sync_session() as session:
                perform_operation(session)
        sequential_time = time.time() - start_time
        print(f"Sequential operations time: {sequential_time:.2f}s")

        # Test 2: Parallel operations
        import concurrent.futures

        start_time = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for _ in range(NUM_OPERATIONS):
                future = executor.submit(
                    lambda: perform_operation(self.database.get_sync_session())
                )
                futures.append(future)
            concurrent.futures.wait(futures)
        parallel_time = time.time() - start_time
        print(f"Parallel operations time: {parallel_time:.2f}s")

    @measure_time
    def test_memory_usage(self):
        """Test memory usage patterns."""
        import os

        import psutil

        process = psutil.Process(os.getpid())

        def get_memory_mb() -> float:
            """Get current memory usage in MB."""
            return process.memory_info().rss / 1024 / 1024

        with self.database.session_scope() as session:
            # Test 1: Load all data at once
            initial_memory = get_memory_mb()
            posts = session.query(Post).all()
            full_load_memory = get_memory_mb()
            print(
                f"Memory usage for full load ({len(posts)} posts): {full_load_memory - initial_memory:.2f}MB"
            )

            # Test 2: Stream data in chunks
            initial_memory = get_memory_mb()
            chunk_size = 1000
            post_count = 0
            for chunk in session.query(Post).yield_per(chunk_size):
                post_count += 1
            stream_memory = get_memory_mb()
            print(f"Memory usage for streaming: {stream_memory - initial_memory:.2f}MB")

            # Test 3: Use scalar queries
            initial_memory = get_memory_mb()
            post_count = session.query(Post).count()
            scalar_memory = get_memory_mb()
            print(
                f"Memory usage for count query ({post_count} posts): {scalar_memory - initial_memory:.2f}MB"
            )

    @measure_time
    def test_query_caching(self):
        """Test query caching patterns."""

        def run_query(session: Session) -> list[tuple[int, str]]:
            return (
                session.query(Post.id, Post.content)
                .filter(Post.accountId == self.performance_data[0].id)
                .all()
            )

        with self.session_scope() as session:
            # Test 1: First query execution
            start_time = time.time()
            posts1 = run_query(session)
            first_time = time.time() - start_time
            print(f"First query time ({len(posts1)} posts): {first_time:.2f}s")

            # Test 2: Second query execution (should use statement cache)
            start_time = time.time()
            posts2 = run_query(session)
            second_time = time.time() - start_time
            print(f"Second query time ({len(posts2)} posts): {second_time:.2f}s")
            assert second_time < first_time
            assert posts1 == posts2  # Results should be identical

            # Test 3: Query with different parameter (should reuse template)
            start_time = time.time()
            posts3 = (
                session.query(Post.id, Post.content)
                .filter(Post.accountId == self.performance_data[1].id)
                .all()
            )
            third_time = time.time() - start_time
            print(f"Third query time ({len(posts3)} posts): {third_time:.2f}s")
            assert third_time < first_time
            # Results should be different since we used a different account
            assert posts3 != posts1
