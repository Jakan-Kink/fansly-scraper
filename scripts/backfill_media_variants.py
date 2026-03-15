#!/usr/bin/env python3
"""Backfill media_variants junction table for existing data.

This is a one-time heuristic backfill for content where the API can no longer
help (expired subscriptions, removed media). It links orphaned downloaded media
(variants) to their probable primary media using accountId match and temporal
proximity (within 60 seconds of createdAt).

Run this ONCE after deploying the variant tracking fix. Subsequent download runs
will populate media_variants automatically via the forward path in
metadata/media.py.

Usage:
    python -m scripts.backfill_media_variants --database-url postgresql://user:pass@host/db
    python -m scripts.backfill_media_variants --config-path config.ini
    python -m scripts.backfill_media_variants  # uses default config.ini
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Add parent directory to path so we can import db_init_scratch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_init_scratch import (
    build_db_url,
    print_safe_db_url,
    resolve_db_settings,
    settings_from_db_url,
)


# The heuristic SQL: link orphaned downloaded media to their probable primaries.
#
# Logic:
# - Find media records that are downloaded (is_downloaded=true, local_filename set)
#   but have NO AccountMedia reference (orphaned variants)
# - Match them to primary media via AccountMedia using same accountId and
#   closest createdAt within 60 seconds
# - Only match video media (variants are primarily HLS/DASH video streams)
# - Use DISTINCT ON to pick the closest temporal match per orphan
# - ON CONFLICT DO NOTHING for idempotent re-runs
BACKFILL_SQL = text("""
INSERT INTO media_variants ("mediaId", "variantId")
SELECT DISTINCT ON (m.id) am."mediaId", m.id
FROM media m
JOIN account_media am ON am."accountId" = m."accountId"
JOIN media pm ON pm.id = am."mediaId"
WHERE m.is_downloaded = true
  AND m.local_filename IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM account_media am2 WHERE am2."mediaId" = m.id)
  AND NOT EXISTS (SELECT 1 FROM media_variants mv WHERE mv."variantId" = m.id)
  AND pm.mimetype LIKE 'video/%'
  AND m."accountId" = pm."accountId"
  AND ABS(EXTRACT(EPOCH FROM (m."createdAt" - pm."createdAt"))) < 60
ORDER BY m.id, ABS(EXTRACT(EPOCH FROM (m."createdAt" - pm."createdAt")))
ON CONFLICT DO NOTHING
""")

# Count query to preview how many rows will be affected
COUNT_SQL = text("""
SELECT COUNT(DISTINCT m.id)
FROM media m
JOIN account_media am ON am."accountId" = m."accountId"
JOIN media pm ON pm.id = am."mediaId"
WHERE m.is_downloaded = true
  AND m.local_filename IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM account_media am2 WHERE am2."mediaId" = m.id)
  AND NOT EXISTS (SELECT 1 FROM media_variants mv WHERE mv."variantId" = m.id)
  AND pm.mimetype LIKE 'video/%'
  AND m."accountId" = pm."accountId"
  AND ABS(EXTRACT(EPOCH FROM (m."createdAt" - pm."createdAt"))) < 60
""")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill media_variants junction table for existing orphaned variants.",
    )
    parser.add_argument(
        "--database-url",
        help="Full PostgreSQL URL (overrides pg_* settings).",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("config.ini"),
        help="Path to config.ini (default: ./config.ini).",
    )
    parser.add_argument("--pg-host", help="PostgreSQL host override.")
    parser.add_argument("--pg-port", type=int, help="PostgreSQL port override.")
    parser.add_argument("--pg-database", help="PostgreSQL database override.")
    parser.add_argument("--pg-user", help="PostgreSQL user override.")
    parser.add_argument(
        "--pg-password",
        help="PostgreSQL password override (or use FANSLY_PG_PASSWORD).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only count how many rows would be inserted, don't modify data.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Build database URL
    if args.database_url:
        db_url = args.database_url
        settings = settings_from_db_url(db_url)
    else:
        settings = resolve_db_settings(args)
        db_url = build_db_url(settings)

    print_safe_db_url(db_url)

    engine = create_engine(db_url)

    with engine.connect() as conn:
        # Check current state
        existing = conn.execute(text("SELECT COUNT(*) FROM media_variants")).scalar()
        print(f"Existing media_variants rows: {existing}")

        # Count candidates
        candidates = conn.execute(COUNT_SQL).scalar()
        print(f"Orphaned variants eligible for backfill: {candidates}")

        if candidates == 0:
            print("Nothing to backfill.")
            return

        if args.dry_run:
            print("Dry run — no changes made.")
            return

        # Execute backfill
        result = conn.execute(BACKFILL_SQL)
        conn.commit()
        print(f"Inserted {result.rowcount} media_variants rows.")

        # Verify
        new_total = conn.execute(text("SELECT COUNT(*) FROM media_variants")).scalar()
        print(f"Total media_variants rows after backfill: {new_total}")


if __name__ == "__main__":
    main()
