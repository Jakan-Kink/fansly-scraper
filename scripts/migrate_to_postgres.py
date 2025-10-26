#!/usr/bin/env python3
"""Migrate SQLite metadata database to PostgreSQL.

This script migrates the Fansly Downloader NG global metadata database
from SQLite to PostgreSQL.

Usage:
    # Basic migration
    python migrate_to_postgres.py \\
        --sqlite-file /path/to/metadata_db.sqlite3 \\
        --pg-host localhost \\
        --pg-database fansly_metadata \\
        --pg-user fansly_user

    # Use environment variable for password
    export FANSLY_PG_PASSWORD=your_password
    python migrate_to_postgres.py ...

Author: Fansly Downloader NG
License: MIT
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Migrate SQLite metadata to PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Source SQLite file
    parser.add_argument(
        "--sqlite-file",
        type=Path,
        default=Path("metadata_db.sqlite3"),
        help="Path to SQLite database file (default: metadata_db.sqlite3)",
    )

    # PostgreSQL connection
    parser.add_argument(
        "--pg-host",
        default="localhost",
        help="PostgreSQL host (default: localhost)",
    )
    parser.add_argument(
        "--pg-port",
        type=int,
        default=5432,
        help="PostgreSQL port (default: 5432)",
    )
    parser.add_argument(
        "--pg-database",
        default="fansly_metadata",
        help="PostgreSQL database name (default: fansly_metadata)",
    )
    parser.add_argument(
        "--pg-user",
        required=True,
        help="PostgreSQL username",
    )
    parser.add_argument(
        "--pg-password",
        help="PostgreSQL password (or use FANSLY_PG_PASSWORD env var)",
    )

    # Migration options
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create backup of SQLite file before migration (default: True)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_false",
        dest="backup",
        help="Skip backup of SQLite file",
    )
    parser.add_argument(
        "--delete-sqlite",
        action="store_true",
        help="Delete SQLite file after successful migration",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Verify data after migration (default: True)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of rows to insert per batch (default: 1000)",
    )

    return parser.parse_args()


def get_password(args: argparse.Namespace) -> str:
    """Get PostgreSQL password from args, env var, or prompt."""
    if args.pg_password:
        return args.pg_password

    password = os.getenv("FANSLY_PG_PASSWORD")
    if password:
        return password

    return getpass.getpass("PostgreSQL password: ")


def create_postgres_connection_url(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
) -> str:
    """Create PostgreSQL connection URL."""
    from urllib.parse import quote_plus

    password_encoded = quote_plus(password)
    return f"postgresql://{user}:{password_encoded}@{host}:{port}/{database}"


def create_tables(sqlite_engine: Engine, pg_engine: Engine) -> None:
    """Create PostgreSQL tables matching the SQLite schema.

    This reflects the SQLite schema and creates matching tables in PostgreSQL,
    with proper type translation from SQLite to PostgreSQL types.
    """
    print("  Creating tables in PostgreSQL...")

    from sqlalchemy import Column, Table
    from sqlalchemy.types import (
        BigInteger,
        Boolean,
        DateTime,
        Float,
        String,
        Text,
    )

    # Reflect the complete schema from SQLite
    sqlite_metadata = MetaData()
    sqlite_metadata.reflect(bind=sqlite_engine)

    # Create new metadata for PostgreSQL with proper types
    pg_metadata = MetaData()

    # Type mapping from SQLite to PostgreSQL
    def translate_type(column_type):
        """Translate SQLite types to PostgreSQL types."""
        type_name = str(column_type).upper()

        if "DATETIME" in type_name or "TIMESTAMP" in type_name:
            return DateTime()
        if "VARCHAR" in type_name or "TEXT" in type_name or "CHAR" in type_name:
            # Extract length if present
            if hasattr(column_type, "length") and column_type.length:
                return String(column_type.length)
            return Text()
        if "INTEGER" in type_name or "INT" in type_name:
            # Use BigInteger for all integers to handle snowflake IDs
            # PostgreSQL BIGINT can handle values up to 9,223,372,036,854,775,807
            return BigInteger()
        if "BOOLEAN" in type_name or "BOOL" in type_name:
            return Boolean()
        if "REAL" in type_name or "FLOAT" in type_name or "DOUBLE" in type_name:
            return Float()
        # Default to Text for unknown types
        return Text()

    # Recreate tables with PostgreSQL-compatible types
    for table_name, sqlite_table in sqlite_metadata.tables.items():
        columns = []
        for col in sqlite_table.columns:
            # Translate the column type
            pg_type = translate_type(col.type)

            # Create new column with translated type
            new_col = Column(
                col.name,
                pg_type,
                primary_key=col.primary_key,
                nullable=col.nullable,
                unique=col.unique,
                autoincrement=col.autoincrement if col.primary_key else False,
            )
            columns.append(new_col)

        # Create table in PostgreSQL metadata
        Table(table_name, pg_metadata, *columns)

    # Create all tables in PostgreSQL
    pg_metadata.create_all(pg_engine)

    print(f"  ✓ Created {len(pg_metadata.tables)} tables")


def get_table_names(engine: Engine) -> list[str]:
    """Get all table names from database, excluding alembic_version."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    # Exclude migration tracking table
    return [t for t in tables if t != "alembic_version"]


def copy_table_data(
    sqlite_engine: Engine,
    pg_engine: Engine,
    table_name: str,
    batch_size: int = 1000,
) -> int:
    """Copy data from SQLite table to PostgreSQL table.

    Returns:
        Number of rows copied
    """
    print(f"  Copying table: {table_name}...", end=" ", flush=True)

    # Get table structure from SQLite
    sqlite_metadata = MetaData()
    sqlite_metadata.reflect(bind=sqlite_engine, only=[table_name])
    sqlite_table = sqlite_metadata.tables[table_name]

    # Get column names
    columns = [col.name for col in sqlite_table.columns]

    # Read data from SQLite
    with sqlite_engine.connect() as sqlite_conn:
        result = sqlite_conn.execute(sqlite_table.select())
        rows = result.fetchall()

    if not rows:
        print("(empty)")
        return 0

    # Write data to PostgreSQL in batches
    total_rows = len(rows)
    with pg_engine.connect() as pg_conn:
        # Get PostgreSQL table
        pg_metadata = MetaData()
        pg_metadata.reflect(bind=pg_engine, only=[table_name])
        pg_table = pg_metadata.tables[table_name]

        # Insert in batches
        for i in range(0, total_rows, batch_size):
            batch = rows[i : i + batch_size]

            # Convert rows to dicts
            batch_dicts = [dict(zip(columns, row)) for row in batch]

            # Insert batch
            pg_conn.execute(pg_table.insert(), batch_dicts)

        pg_conn.commit()

    print(f"{total_rows} rows")
    return total_rows


def verify_migration(
    sqlite_engine: Engine,
    pg_engine: Engine,
) -> bool:
    """Verify that migration was successful.

    Returns:
        True if verification passed, False otherwise
    """
    print("  Verifying migration...")

    # Get table names from both databases
    sqlite_tables = set(get_table_names(sqlite_engine))
    pg_tables = set(get_table_names(pg_engine))

    # Check all tables exist
    missing_tables = sqlite_tables - pg_tables
    if missing_tables:
        print(f"  ✗ Missing tables in PostgreSQL: {missing_tables}")
        return False

    # Check row counts for each table
    all_match = True
    for table_name in sqlite_tables:
        # Get SQLite count
        with sqlite_engine.connect() as sqlite_conn:
            sqlite_count = sqlite_conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()

        # Get PostgreSQL count
        with pg_engine.connect() as pg_conn:
            pg_count = pg_conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()

        if sqlite_count != pg_count:
            print(
                f"  ✗ Row count mismatch for {table_name}: "
                f"SQLite={sqlite_count}, PostgreSQL={pg_count}"
            )
            all_match = False

    if all_match:
        print("  ✓ Verification passed")
        return True
    print("  ✗ Verification failed")
    return False


def backup_sqlite_file(sqlite_file: Path) -> Path:
    """Create backup of SQLite database file.

    Returns:
        Path to backup file
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_file = sqlite_file.with_suffix(f".backup_{timestamp}.sqlite3")

    print(f"  Creating backup: {backup_file.name}")
    shutil.copy2(sqlite_file, backup_file)

    # Also backup WAL and SHM files if they exist
    for ext in ["-wal", "-shm"]:
        wal_file = Path(str(sqlite_file) + ext)
        if wal_file.exists():
            backup_wal = Path(str(backup_file) + ext)
            shutil.copy2(wal_file, backup_wal)

    return backup_file


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Check SQLite file exists
    if not args.sqlite_file.exists():
        print(f"Error: SQLite file not found: {args.sqlite_file}", file=sys.stderr)
        return 1

    # Get password
    password = get_password(args)

    # Create PostgreSQL connection
    pg_url = create_postgres_connection_url(
        args.pg_host,
        args.pg_port,
        args.pg_database,
        args.pg_user,
        password,
    )

    print("=" * 70)
    print("SQLite to PostgreSQL Migration")
    print("=" * 70)
    print(f"Source: {args.sqlite_file}")
    print(f"Target: {args.pg_user}@{args.pg_host}:{args.pg_port}/{args.pg_database}")
    print(f"Backup: {args.backup}")
    print(f"Delete SQLite: {args.delete_sqlite}")
    print(f"Dry run: {args.dry_run}")
    print("=" * 70)

    if args.dry_run:
        print("\n[DRY RUN] Would migrate this database")
        return 0

    # Test PostgreSQL connection
    try:
        pg_engine = create_engine(pg_url, pool_pre_ping=True)
        with pg_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("\n✓ PostgreSQL connection successful")
    except Exception as e:
        print(f"\n✗ PostgreSQL connection failed: {e}", file=sys.stderr)
        return 1

    try:
        # Backup SQLite file
        if args.backup:
            backup_path = backup_sqlite_file(args.sqlite_file)
            print(f"Created backup: {backup_path}")

        # Create SQLite engine
        sqlite_url = f"sqlite:///{args.sqlite_file}"
        sqlite_engine = create_engine(sqlite_url)

        # Create tables in PostgreSQL by reflecting SQLite schema
        create_tables(sqlite_engine, pg_engine)

        # Get list of tables to copy
        tables = get_table_names(sqlite_engine)
        print(f"\n  Tables to copy: {len(tables)}")

        # Copy each table
        total_rows = 0
        for table_name in tables:
            rows_copied = copy_table_data(
                sqlite_engine,
                pg_engine,
                table_name,
                batch_size=args.batch_size,
            )
            total_rows += rows_copied

        print(f"\n  Total rows copied: {total_rows}")

        # Verify migration
        if args.verify:
            if not verify_migration(sqlite_engine, pg_engine):
                print("\n  ✗ Migration verification failed!")
                return 1

        # Set up Alembic version tracking
        # Stamp to the last migration before the Boolean conversion (06658bf47c03)
        # so that the is_downloaded Integer->Boolean migration will run properly
        print("\n  Setting up Alembic version tracking...")
        with pg_engine.connect() as conn:
            # Create alembic_version table if it doesn't exist
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                    ")"
                )
            )
            # Stamp to the revision before is_downloaded Boolean migration
            conn.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('06658bf47c03') "
                    "ON CONFLICT (version_num) DO NOTHING"
                )
            )
            conn.commit()
        print(
            "  ✓ Alembic version set to 06658bf47c03 (before is_downloaded Boolean migration)"
        )

        # Delete SQLite file if requested
        if args.delete_sqlite:
            print(f"\n  Deleting SQLite file: {args.sqlite_file.name}")
            args.sqlite_file.unlink()
            # Also delete WAL and SHM files
            for ext in ["-wal", "-shm"]:
                wal_file = Path(str(args.sqlite_file) + ext)
                if wal_file.exists():
                    wal_file.unlink()

        print("\n✓ Migration completed successfully!")

        if not args.delete_sqlite:
            print(
                "\nNote: SQLite file was not deleted. Use --delete-sqlite to remove it."
            )

        return 0

    except Exception as e:
        print(f"\n✗ Migration failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
