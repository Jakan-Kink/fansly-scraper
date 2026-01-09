#!/usr/bin/env python3
"""Migrate legacy hash2 filenames into PostgreSQL.

This is meant for migrating existing hash2-based filenames produced by
prof79/fansly-downloader-ng v0.9.9 into the modified Jakan-Kink/fansly-scraper
fork without touching application code.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import create_engine

from scripts.db_init_scratch import (
    build_db_url,
    clean_database,
    ensure_fk_deferrable,
    ensure_media_locations_fk_deferrable,
    ensure_media_locations_unique,
    ensure_tables_exist,
    print_safe_db_url,
    resolve_db_settings,
    run_dedupe_init,
    run_statements,
    settings_from_db_url,
    sql_for_create,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate hash2 legacy files into the PostgreSQL database.",
    )
    parser.add_argument(
        "--database-url",
        help="Full PostgreSQL URL (overrides pg_* settings).",
    )
    parser.add_argument(
        "--schema",
        default="public",
        help="Target schema (default: public).",
    )
    parser.add_argument(
        "--pg-host",
        help="PostgreSQL host override.",
    )
    parser.add_argument(
        "--pg-port",
        type=int,
        help="PostgreSQL port override.",
    )
    parser.add_argument(
        "--pg-database",
        help="PostgreSQL database override.",
    )
    parser.add_argument(
        "--pg-user",
        help="PostgreSQL user override.",
    )
    parser.add_argument(
        "--pg-password",
        help="PostgreSQL password override (or use FANSLY_PG_PASSWORD).",
    )
    parser.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help="Skip wiping data from tables before migration.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts for destructive actions.",
    )
    parser.add_argument(
        "--dedupe-creators",
        help="Comma/space-separated creator list (defaults to config.ini).",
    )
    parser.add_argument(
        "--creator-ids",
        help="Optional mapping like 'name=id,name2=id2' to avoid API lookup.",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Skip API lookup when resolving creator IDs.",
    )
    parser.set_defaults(clean=True)
    return parser.parse_args()


def _prepare_db(args: argparse.Namespace, db_url: str) -> None:
    """Prepare the database for the hash2 migration.

    This ensures the stub trigger exists and constraints are set to deferrable,
    then optionally wipes tables to match the fresh-DB bootstrap flow.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            ensure_tables_exist(conn, args.schema)
            if args.clean:
                clean_database(conn, args.schema, dry_run=False, assume_yes=args.yes)

            # Keep FK behavior consistent with db_init_scratch.py.
            ensure_media_locations_unique(conn, args.schema, dry_run=False)
            ensure_media_locations_fk_deferrable(conn, args.schema, dry_run=False)
            ensure_fk_deferrable(
                conn=conn,
                schema=args.schema,
                table="account_media_bundle_media",
                column="media_id",
                ref_table="account_media",
                ref_column="id",
                desired_name="fk_account_media_bundle_media_media_deferrable",
                dry_run=False,
            )
            ensure_fk_deferrable(
                conn=conn,
                schema=args.schema,
                table="account_media_bundle_media",
                column="bundle_id",
                ref_table="account_media_bundles",
                ref_column="id",
                desired_name="fk_account_media_bundle_media_bundle_deferrable",
                dry_run=False,
            )

            statements = sql_for_create(args.schema, backfill=True)
            run_statements(conn, statements, dry_run=False)
    finally:
        engine.dispose()


def main() -> int:
    args = parse_args()
    args.config_path = Path("config.ini")
    args.fix_hash2_db = True

    if args.database_url:
        db_url = args.database_url
        settings = settings_from_db_url(db_url) or resolve_db_settings(args)
    else:
        settings = resolve_db_settings(args)
        db_url = build_db_url(settings)

    print_safe_db_url(db_url)
    _prepare_db(args, db_url)
    print("Created media stub trigger successfully.")

    asyncio.run(run_dedupe_init(args, settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
