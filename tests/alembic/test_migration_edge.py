"""Edge case tests for Alembic migrations using real database state.

These tests create specific database states that existed before migrations ran,
then execute the migrations to hit all branches including idempotency guards.

Tests cover:
- 6dcb1d898d8b: FK drops and index creation
- 4416b99f028e: Index and constraint edge cases
- 7f057c9b00e0: Primary key drops and unique constraint handling
- 1941514875f1: Malformed hashtag cleanup edge case
- 0c4cb91b36d5: Case-insensitive hashtag handling
"""

from itertools import groupby as original_groupby
from unittest.mock import patch
from urllib.parse import quote_plus

from alembic.config import Config
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy import (
    inspect as sa_inspect,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from alembic import command


class Test6dcbEdgeCases:
    """Test 6dcb1d898d8b migration with real database state to hit FK drops and index creation."""

    def test_6dcb_upgrade_with_old_fk_constraints_and_no_index(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 6dcb upgrade hits all 4 FK drop branches and index creation (lines 43, 45, 50, 56, 78).

        This creates a synthetic schema with ALL old FK names that 6dcb checks for,
        even though this exact combination may not have existed in real migration history.
        The goal is to achieve 100% code coverage by exercising all conditional branches.
        """
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations UP TO the revision BEFORE 6dcb to get base schema
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "f8df81787709")

        # Now create a synthetic edge case: add ALL the old FK names that 6dcb will check for
        # This might not reflect real history, but ensures 100% branch coverage
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # First, drop ALL existing FKs on account_media to start clean
            result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'account_media' AND constraint_type = 'FOREIGN KEY'"
                )
            )
            existing_fks = [row[0] for row in result]
            for fk_name in existing_fks:
                conn.execute(
                    text(f'ALTER TABLE account_media DROP CONSTRAINT "{fk_name}"')
                )

            # Add ALL FOUR old FK constraint names that 6dcb migration checks for
            conn.execute(
                text(
                    'ALTER TABLE account_media ADD CONSTRAINT "account_media_accountId_fkey" '
                    'FOREIGN KEY ("accountId") REFERENCES accounts(id)'
                )
            )
            conn.execute(
                text(
                    'ALTER TABLE account_media ADD CONSTRAINT "account_media_mediaId_fkey" '
                    'FOREIGN KEY ("mediaId") REFERENCES media(id)'
                )
            )
            conn.execute(
                text(
                    'ALTER TABLE account_media ADD CONSTRAINT "fk_account_media_accountId_accounts" '
                    'FOREIGN KEY ("accountId") REFERENCES accounts(id)'
                )
            )
            conn.execute(
                text(
                    'ALTER TABLE account_media ADD CONSTRAINT "fk_account_media_mediaId_media" '
                    'FOREIGN KEY ("mediaId") REFERENCES media(id)'
                )
            )

            # Drop ix_account_media_accountId index if it exists (forces line 78 to execute)
            conn.execute(text('DROP INDEX IF EXISTS "ix_account_media_accountId"'))

        engine.dispose()

        # Now run 6dcb upgrade
        command.upgrade(alembic_config, "6dcb1d898d8b")

        # Verify the migration succeeded by checking the final state
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Check that old FKs were dropped and new ones created
            fk_result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'account_media' AND constraint_type = 'FOREIGN KEY'"
                )
            )
            fk_names = [row[0] for row in fk_result]

            # Old constraint names should be gone
            assert "account_media_accountId_fkey" not in fk_names
            assert "account_media_mediaId_fkey" not in fk_names

            # New constraint names should exist
            assert "fk_account_media_accountId_accounts" in fk_names
            assert "fk_account_media_mediaId_media" in fk_names

            # Check that index was created (line 78)
            idx_result = conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'account_media'"
                )
            )
            idx_names = [row[0] for row in idx_result]
            assert "ix_account_media_accountId" in idx_names

            # Verify alembic_version was updated
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "6dcb1d898d8b"

        engine.dispose()


class Test4416EdgeCases:
    """Test 4416b99f028e migration edge cases."""

    def test_4416_upgrade_without_existing_constraints(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 4416 upgrade when pk_post_hashtags doesn't exist (line 50-51)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)

        metadata = MetaData()

        # Create hashtags table WITHOUT ix_hashtags_value index
        hashtags_table = Table(
            "hashtags",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("value", String),
        )

        # Create post_hashtags WITHOUT pk_post_hashtags constraint
        post_hashtags_table = Table(
            "post_hashtags",
            metadata,
            Column("postId", Integer),
            Column("hashtagId", Integer),
        )

        with engine.begin() as conn:
            conn.execute(CreateTable(hashtags_table))
            conn.execute(CreateTable(post_hashtags_table))

            # Create alembic_version
            conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            conn.execute(text("INSERT INTO alembic_version VALUES ('cc6fe4e9ef45')"))

        engine.dispose()

        # Run upgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "4416b99f028e")

        # Verify index and constraint were created
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            idx_result = conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'hashtags'")
            )
            idx_names = [row[0] for row in idx_result]
            assert "ix_hashtags_value" in idx_names

            constraint_result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'post_hashtags' AND constraint_type = 'UNIQUE'"
                )
            )
            constraint_names = [row[0] for row in constraint_result]
            assert "pk_post_hashtags" in constraint_names

        engine.dispose()

    def test_4416_downgrade_without_index(self, uuid_test_db_factory, tmp_path):
        """Test 4416 downgrade when ix_hashtags_value doesn't exist (lines 67-68)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)

        metadata = MetaData()

        hashtags_table = Table(
            "hashtags",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("value", String),
            # No index created
        )

        post_hashtags_table = Table(
            "post_hashtags",
            metadata,
            Column("postId", Integer),
            Column("hashtagId", Integer),
            UniqueConstraint("postId", "hashtagId", name="pk_post_hashtags"),
        )

        with engine.begin() as conn:
            conn.execute(CreateTable(hashtags_table))
            conn.execute(CreateTable(post_hashtags_table))

            conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            # Set version to 4416 (after upgrade)
            conn.execute(text("INSERT INTO alembic_version VALUES ('4416b99f028e')"))

        engine.dispose()

        # Run downgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.downgrade(alembic_config, "cc6fe4e9ef45")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Verify downgrade completed
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "cc6fe4e9ef45"

        engine.dispose()


class Test7f05EdgeCases:
    """Test 7f057c9b00e0 migration edge cases."""

    def test_7f05_upgrade_without_existing_pk(self, uuid_test_db_factory, tmp_path):
        """Test 7f05 upgrade when post_mentions_pkey doesn't exist (line 56)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 1941514875f1 (before 7f05)
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "1941514875f1")

        # Modify the schema to remove the primary key (edge case)
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Drop the primary key constraint if it exists
            conn.execute(
                text(
                    'ALTER TABLE post_mentions DROP CONSTRAINT IF EXISTS "post_mentions_pkey"'
                )
            )

        engine.dispose()

        # Run upgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "7f057c9b00e0")

        # Verify migration succeeded
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Check primary key was created
            pk_result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'post_mentions' AND constraint_type = 'PRIMARY KEY'"
                )
            )
            pk_names = [row[0] for row in pk_result]
            assert "post_mentions_pkey" in pk_names

        engine.dispose()

    def test_7f05_downgrade_without_unique_constraints(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 7f05 downgrade when unique constraints don't exist (lines 88, 91->95)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 7f057c9b00e0 (after upgrade)
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "7f057c9b00e0")

        # Modify schema to remove unique constraints (edge case)
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Drop unique constraints to test the downgrade edge case
            conn.execute(
                text(
                    'ALTER TABLE post_mentions DROP CONSTRAINT IF EXISTS "uix_post_mentions_handle"'
                )
            )
            conn.execute(
                text(
                    'ALTER TABLE post_mentions DROP CONSTRAINT IF EXISTS "uix_post_mentions_account"'
                )
            )

        engine.dispose()

        # Run downgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.downgrade(alembic_config, "1941514875f1")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

        engine.dispose()

    def test_7f05_downgrade_without_pk(self, uuid_test_db_factory, tmp_path):
        """Test 7f05 downgrade when PK doesn't exist - covers branch 91->95."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 7f057c9b00e0
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "7f057c9b00e0")

        # Drop PK before downgrade to test branch 91->95
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            conn.execute(
                text(
                    'ALTER TABLE post_mentions DROP CONSTRAINT IF EXISTS "post_mentions_pkey"'
                )
            )

        engine.dispose()

        # Run downgrade - should skip PK drop since it doesn't exist
        command.downgrade(alembic_config, "1941514875f1")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

        engine.dispose()

    def test_7f05_upgrade_with_old_constraint(self, uuid_test_db_factory, tmp_path):
        """Test 7f05 upgrade with old constraint existing - covers line 56."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 1941514875f1 (before 7f05)
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "1941514875f1")

        # Manually create the old unique constraint for testing line 56
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            conn.execute(
                text(
                    'ALTER TABLE post_mentions ADD CONSTRAINT "post_mentions_postId_accountId_key" '
                    'UNIQUE ("postId", "accountId")'
                )
            )

        engine.dispose()

        # Run upgrade to 7f057c9b00e0 - this should drop the old constraint (line 56)
        command.upgrade(alembic_config, "7f057c9b00e0")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Verify migration succeeded
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "7f057c9b00e0"

            # Verify old constraint was dropped (this exercises line 56)
            constraint_result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'post_mentions' AND constraint_name = 'post_mentions_postId_accountId_key'"
                )
            )
            old_constraint = constraint_result.fetchone()
            assert old_constraint is None

        engine.dispose()

    def test_7f05_downgrade_with_only_handle_constraint(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 7f05 downgrade when only handle constraint exists - covers line 88 and branch 89->91."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 7f057c9b00e0
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "7f057c9b00e0")

        # Manually create the handle constraint for testing (it's redundant with PK, so normally not created)
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Drop account constraint but manually create handle constraint
            conn.execute(
                text(
                    'ALTER TABLE post_mentions DROP CONSTRAINT IF EXISTS "uix_post_mentions_account"'
                )
            )
            conn.execute(
                text(
                    'ALTER TABLE post_mentions ADD CONSTRAINT "uix_post_mentions_handle" '
                    'UNIQUE ("postId", handle)'
                )
            )

        engine.dispose()

        # Run downgrade - should drop handle constraint (line 88) and skip account constraint (branch 89->91)
        command.downgrade(alembic_config, "1941514875f1")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Verify migration succeeded
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

            # Verify handle constraint was dropped
            constraint_result = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'post_mentions' AND constraint_name = 'uix_post_mentions_handle'"
                )
            )
            handle_constraint = constraint_result.fetchone()
            assert handle_constraint is None

        engine.dispose()


class Test1941EdgeCases:
    """Test 1941514875f1 migration edge cases."""

    def test_1941_no_conflicting_posts_to_copy(self, uuid_test_db_factory, tmp_path):
        """Test 1941 when posts_to_copy is empty (line 142)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)

        # Create schema with malformed hashtag that will create a NEW hashtag
        # but all posts already have the new hashtag
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE hashtags (id SERIAL PRIMARY KEY, value VARCHAR, stash_id INTEGER)"
                )
            )
            conn.execute(
                text(
                    'CREATE TABLE post_hashtags ("postId" INTEGER, "hashtagId" INTEGER)'
                )
            )

            # Insert malformed hashtag (will be split into multiple)
            result = conn.execute(
                text("INSERT INTO hashtags (value) VALUES ('#foo#bar') RETURNING id")
            )
            malformed_id = result.scalar()

            # Insert posts with this malformed hashtag
            conn.execute(
                text(
                    f"INSERT INTO post_hashtags VALUES (100, {malformed_id}), (101, {malformed_id})"
                )
            )

            # But also insert 'foo' as a separate hashtag that posts ALREADY have
            result2 = conn.execute(
                text("INSERT INTO hashtags (value) VALUES ('foo') RETURNING id")
            )
            foo_id = result2.scalar()
            conn.execute(
                text(
                    f"INSERT INTO post_hashtags VALUES (100, {foo_id}), (101, {foo_id})"
                )
            )

            # So when migration extracts 'foo' from '#foo#bar', it will find existing hashtag
            # but posts_to_copy will be empty because posts 100 and 101 already have hashtagId=2

            conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            conn.execute(text("INSERT INTO alembic_version VALUES ('0c4cb91b36d5')"))

        engine.dispose()

        # Run upgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "1941514875f1")

        # Verify migration completed
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

            # The malformed hashtag should have been removed
            hashtag_result = conn.execute(
                text("SELECT id, value FROM hashtags ORDER BY id")
            )
            hashtags = [(row[0], row[1]) for row in hashtag_result]

            # Should only have the clean hashtags, not the malformed one
            hashtag_values = [h[1] for h in hashtags]
            assert "#foo#bar" not in hashtag_values

        engine.dispose()

    def test_1941_posts_to_copy_with_new_hashtags(self, uuid_test_db_factory, tmp_path):
        """Test 1941 line 142 (posts_to_copy with new hashtags being created)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)

        # Create schema
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE hashtags (id SERIAL PRIMARY KEY, value VARCHAR, stash_id INTEGER)"
                )
            )
            conn.execute(
                text(
                    'CREATE TABLE post_hashtags ("postId" INTEGER, "hashtagId" INTEGER)'
                )
            )

            # Insert malformed hashtag that will be split into TWO NEW hashtags
            result = conn.execute(
                text("INSERT INTO hashtags (value) VALUES ('#new1#new2') RETURNING id")
            )
            malformed_id = result.scalar()

            # Add posts that reference the malformed hashtag
            conn.execute(
                text(
                    f"INSERT INTO post_hashtags VALUES (200, {malformed_id}), (201, {malformed_id})"
                )
            )

            # Don't create 'new1' or 'new2' as existing hashtags
            # So both will be created and posts will be copied (line 142)

            conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            conn.execute(text("INSERT INTO alembic_version VALUES ('0c4cb91b36d5')"))

        engine.dispose()

        # Run upgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "1941514875f1")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

            # The malformed hashtag should have been removed
            hashtag_result = conn.execute(
                text("SELECT value FROM hashtags ORDER BY id")
            )
            hashtags = [row[0] for row in hashtag_result]

            # New hashtags should exist
            assert "new1" in hashtags
            assert "new2" in hashtags
            assert "#new1#new2" not in hashtags

        engine.dispose()

    def test_1941_no_conflict_posts_and_empty_posts_to_copy(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 1941 branch 97->113 (no conflict_posts when existing hashtag) and posts_to_copy copying (line 142)."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)

        # Create schema that will trigger both branches
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE hashtags (id SERIAL PRIMARY KEY, value VARCHAR, stash_id INTEGER)"
                )
            )
            conn.execute(
                text(
                    'CREATE TABLE post_hashtags ("postId" INTEGER, "hashtagId" INTEGER)'
                )
            )

            # Insert malformed hashtag that will be split into 'test' and 'value'
            result = conn.execute(
                text("INSERT INTO hashtags (value) VALUES ('#test#value') RETURNING id")
            )
            malformed_id = result.scalar()

            # Insert 'test' as an existing hashtag (so branch 97->113 is tested when existing is truthy)
            result2 = conn.execute(
                text("INSERT INTO hashtags (value) VALUES ('test') RETURNING id")
            )
            test_id = result2.scalar()

            # Add a post that references the malformed hashtag but NOT the existing 'test'
            # This means conflict_posts will be empty (branch 97->113 is False)
            # but there's still a post to update
            conn.execute(
                text(f"INSERT INTO post_hashtags VALUES (100, {malformed_id})")
            )

            # For 'value', it doesn't exist yet, so a new hashtag will be created
            # The post 100 references malformed_id, and after 'test' is processed,
            # the post still references malformed_id, so when 'value' is extracted,
            # posts_to_copy will include post 100 (line 142 gets executed)

            conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            conn.execute(text("INSERT INTO alembic_version VALUES ('0c4cb91b36d5')"))

        engine.dispose()

        # Run upgrade
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "1941514875f1")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "1941514875f1"

            # The malformed hashtag should have been removed
            hashtag_result = conn.execute(
                text("SELECT value FROM hashtags WHERE value NOT LIKE '#%#%'")
            )
            hashtags = [row[0] for row in hashtag_result]

            # Clean hashtags should exist
            assert "test" in hashtags
            assert "value" in hashtags

        engine.dispose()


class Test0c4cbEdgeCases:
    """Test 0c4cb91b36d5 migration edge cases."""

    def test_0c4cb_empty_group(self, uuid_test_db_factory, tmp_path):
        """Test 0c4cb normal path with case-variant duplicates - covers line 67, branch 73->64."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migrations up to 4416b99f028e (before 0c4cb)
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "4416b99f028e")

        # Add case-variant duplicates - groupby will group them and merge
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Insert case variants of the same hashtag
            conn.execute(
                text(
                    "INSERT INTO hashtags (value) VALUES ('Python'), ('python'), ('PYTHON')"
                )
            )

        engine.dispose()

        # Run upgrade to 0c4cb - should merge the duplicates
        command.upgrade(alembic_config, "0c4cb91b36d5")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "0c4cb91b36d5"

        engine.dispose()

    def test_0c4cb_with_empty_and_single_item_groups(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test 0c4cb with mock groupby yielding empty and single-item groups - covers line 67 and branch 73->64."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"

        # Run migrations up to 4416b99f028e
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "4416b99f028e")

        # Mock groupby to return normal groups, an empty group, AND a single-item group
        def mock_groupby(iterable, key=None):
            # First yield the normal groups from original groupby
            groups = list(original_groupby(iterable, key=key))
            yield from groups
            # Then yield an artificial empty group to hit line 67
            yield "empty", iter([])
            # And yield a single-item group to hit branch 73->64 (when duplicates is falsy)
            yield "single", iter([("single-item",)])

        # Patch itertools.groupby globally so the migration will use it
        with patch("itertools.groupby", mock_groupby):
            command.upgrade(alembic_config, "0c4cb91b36d5")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "0c4cb91b36d5"

        engine.dispose()


class Test2dc7EdgeCases:
    """Test 2dc7238fee2b migration edge cases."""

    def test_2dc7_downgrade_with_existing_fks(self, uuid_test_db_factory, tmp_path):
        """Test 2dc7 downgrade when FKs already exist - covers branches 122->131, 136->146."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migration up to 2dc7238fee2b
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "2dc7238fee2b")

        # Manually verify or create the FK constraints before downgrade
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # Check if FKs exist and create them if not
            inspector = sa_inspect(conn)

            post_mentions_fks = [
                fk["name"]
                for fk in inspector.get_foreign_keys("post_mentions")
                if fk["constrained_columns"] == ["accountId"]
            ]
            if not post_mentions_fks:
                conn.execute(
                    text(
                        'ALTER TABLE post_mentions ADD CONSTRAINT "post_mentions_accountId_fkey" '
                        'FOREIGN KEY ("accountId") REFERENCES accounts(id)'
                    )
                )

            group_users_fks = [
                fk["name"]
                for fk in inspector.get_foreign_keys("group_users")
                if fk["constrained_columns"] == ["accountId"]
            ]
            if not group_users_fks:
                conn.execute(
                    text(
                        'ALTER TABLE group_users ADD CONSTRAINT "group_users_accountId_fkey" '
                        'FOREIGN KEY ("accountId") REFERENCES accounts(id)'
                    )
                )

        engine.dispose()

        # Run downgrade - should skip FK creation since they exist (lines 122, 136)
        command.downgrade(alembic_config, "7f057c9b00e0")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "7f057c9b00e0"

        engine.dispose()


class Testb8dcEdgeCases:
    """Test b8dcecc1e979 migration edge cases."""

    def test_b8dc_downgrade_without_matching_fk_and_constraint(
        self, uuid_test_db_factory, tmp_path
    ):
        """Test b8dc downgrade when FK/constraint not found in loops - covers branches 458->457, 617->612."""
        config = uuid_test_db_factory
        password_encoded = quote_plus(config.pg_password) if config.pg_password else ""
        db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
        engine = create_engine(db_url, poolclass=NullPool)
        engine.dispose()

        # Run migration up to b8dcecc1e979
        alembic_ini = tmp_path / "alembic.ini"
        alembic_ini.write_text(
            f"""[alembic]
script_location = alembic
sqlalchemy.url = {db_url}
"""
        )

        alembic_config = Config(str(alembic_ini))
        command.upgrade(alembic_config, "b8dcecc1e979")

        # Manipulate FKs and constraints so loops iterate but conditions are False
        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            # For branch 458->457: Create an FK that doesn't match the condition
            # Drop the matching FK (accounts/authorId) and add a different one
            conn.execute(
                text(
                    'ALTER TABLE stories DROP CONSTRAINT IF EXISTS "stories_authorId_fkey"'
                )
            )
            # Add an FK that refers to a different table or column (condition will be False)
            # Create a dummy FK to a different column to make the loop iterate but not match
            conn.execute(
                text(
                    'ALTER TABLE stories ADD CONSTRAINT "stories_dummy_fk" '
                    "FOREIGN KEY (id) REFERENCES stories(id)"
                )
            )

            # For branch 617->612: Drop the matching constraint and add a different one
            # Drop the mediaId+variantId unique constraint
            conn.execute(
                text(
                    "ALTER TABLE media_variants DROP CONSTRAINT IF EXISTS "
                    '"media_variants_mediaId_variantId_key"'
                )
            )
            # Add a unique constraint on different columns (condition will be False)
            conn.execute(
                text(
                    'ALTER TABLE media_variants ADD CONSTRAINT "media_variants_dummy_uniq" '
                    'UNIQUE ("mediaId")'
                )
            )

        engine.dispose()

        # Run downgrade - loops should not find matches and continue without breaking
        command.downgrade(alembic_config, "2dc7238fee2b")

        engine = create_engine(db_url, poolclass=NullPool)
        with engine.begin() as conn:
            version_result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = version_result.scalar()
            assert version == "2dc7238fee2b"

        engine.dispose()
