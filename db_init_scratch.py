#!/usr/bin/env python3
"""Initialize PostgreSQL for filesystem-first media import.

Creates a trigger that inserts stub accounts before media rows are written,
so foreign key checks pass during initial dedupe imports. Can also reset the
database contents for a clean bootstrap.
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import getpass
import os
import re
import sys
import mimetypes
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

from sqlalchemy import create_engine, text


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize PostgreSQL for media-first imports (stub accounts).",
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
        "--schema",
        default="public",
        help="Target schema (default: public).",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop the trigger/function instead of creating them.",
    )
    parser.add_argument(
        "--clean",
        dest="clean",
        action="store_true",
        default=True,
        help="Wipe all data from tables before applying fixes (default: on).",
    )
    parser.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help="Skip wiping data from tables.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts for destructive actions.",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip backfilling missing accounts from existing media rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL statements without executing them (also skips file changes).",
    )
    parser.add_argument(
        "--run-dedupe-init",
        action="store_true",
        help="Run dedupe_init after database setup to register existing files.",
    )
    parser.add_argument(
        "--dedupe-creators",
        help="Comma/space-separated creator list for dedupe_init (defaults to config.ini).",
    )
    parser.add_argument(
        "--creator-ids",
        help="Optional mapping like 'name=id,name2=id2' to avoid API lookup.",
    )
    parser.add_argument(
        "--print-creator-ids",
        action="store_true",
        help="Resolve creator IDs and print the mapping, then exit.",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Skip API lookup when resolving creator IDs.",
    )
    parser.add_argument(
        "--fix-hash2-db",
        action="store_true",
        help="After dedupe_init, mark hash2 files as downloaded in the DB.",
    )
    return parser.parse_args()


def load_config_values(config_path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    if config_path.exists():
        parser.read(config_path)
    if not parser.has_section("Options"):
        return {}
    options = parser["Options"]
    return {
        "pg_host": options.get("pg_host", fallback=None),
        "pg_port": options.get("pg_port", fallback=None),
        "pg_database": options.get("pg_database", fallback=None),
        "pg_user": options.get("pg_user", fallback=None),
        "pg_password": options.get("pg_password", fallback=None),
    }


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def resolve_db_settings(args: argparse.Namespace) -> dict[str, str]:
    config_values = load_config_values(args.config_path)
    env_host = os.getenv("FANSLY_PG_HOST")
    env_port = os.getenv("FANSLY_PG_PORT")
    env_database = os.getenv("FANSLY_PG_DATABASE")
    env_user = os.getenv("FANSLY_PG_USER")
    env_password = os.getenv("FANSLY_PG_PASSWORD")

    host = first_non_empty(args.pg_host, env_host, config_values.get("pg_host")) or "localhost"
    port = first_non_empty(
        str(args.pg_port) if args.pg_port is not None else None,
        env_port,
        config_values.get("pg_port"),
    ) or "5432"
    database = (
        first_non_empty(args.pg_database, env_database, config_values.get("pg_database"))
        or "fansly_metadata"
    )
    user = first_non_empty(args.pg_user, env_user, config_values.get("pg_user")) or "fansly_user"

    password = first_non_empty(args.pg_password, env_password, config_values.get("pg_password"))
    if password is None and sys.stdin.isatty():
        password = getpass.getpass("PostgreSQL password (leave blank for none): ")
    if password is None:
        password = ""

    return {
        "pg_host": host,
        "pg_port": port,
        "pg_database": database,
        "pg_user": user,
        "pg_password": password,
    }


def build_db_url(settings: dict[str, str]) -> str:
    password_encoded = quote_plus(settings["pg_password"])
    return (
        f"postgresql://{settings['pg_user']}:{password_encoded}"
        f"@{settings['pg_host']}:{settings['pg_port']}/{settings['pg_database']}"
    )


def settings_from_db_url(db_url: str) -> dict[str, str] | None:
    parsed = urlparse(db_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return None
    if not parsed.hostname or not parsed.path:
        return None
    return {
        "pg_host": parsed.hostname,
        "pg_port": str(parsed.port or 5432),
        "pg_database": parsed.path.lstrip("/"),
        "pg_user": parsed.username or "fansly_user",
        "pg_password": unquote(parsed.password or ""),
    }


def validate_identifier(name: str) -> str:
    if not IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def sql_for_create(schema: str, backfill: bool) -> list[str]:
    schema = validate_identifier(schema)
    schema_ident = f'"{schema}"'
    function_name = f"{schema_ident}.ensure_account_stub"

    statements = [
        f"""
CREATE OR REPLACE FUNCTION {function_name}()
RETURNS trigger AS $$
BEGIN
  IF NEW."accountId" IS NULL THEN
    RETURN NEW;
  END IF;

  -- Media rows can arrive before accounts on first bootstrap; insert a stub.
  INSERT INTO {schema_ident}.accounts ("id", "username")
  VALUES (NEW."accountId", 'stub_' || NEW."accountId")
  ON CONFLICT DO NOTHING;

  -- Track stubs if the optional table exists (used for later enrichment).
  IF to_regclass('{schema}.stub_tracker') IS NOT NULL THEN
    INSERT INTO {schema_ident}.stub_tracker ("table_name", "record_id", "created_at", "reason")
    VALUES ('accounts', NEW."accountId", now(), 'media import')
    ON CONFLICT ("table_name", "record_id") DO NOTHING;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""".strip(),
        f"DROP TRIGGER IF EXISTS media_account_stub ON {schema_ident}.media;",
        f"""
CREATE TRIGGER media_account_stub
BEFORE INSERT OR UPDATE OF "accountId" ON {schema_ident}.media
FOR EACH ROW EXECUTE FUNCTION {function_name}();
""".strip(),
    ]

    if backfill:
        statements.append(
            f"""
INSERT INTO {schema_ident}.accounts ("id", "username")
SELECT DISTINCT m."accountId", 'stub_' || m."accountId"
FROM {schema_ident}.media m
LEFT JOIN {schema_ident}.accounts a ON a.id = m."accountId"
WHERE m."accountId" IS NOT NULL AND a.id IS NULL;
""".strip()
        )

    return statements


def sql_for_drop(schema: str) -> list[str]:
    schema = validate_identifier(schema)
    schema_ident = f'"{schema}"'
    function_name = f"{schema_ident}.ensure_account_stub"
    return [
        f"DROP TRIGGER IF EXISTS media_account_stub ON {schema_ident}.media;",
        f"DROP FUNCTION IF EXISTS {function_name}();",
    ]


def print_safe_db_url(db_url: str) -> None:
    safe_url = re.sub(r":([^:@/]+)@", ":***@", db_url)
    print(f"Using database: {safe_url}")


def ensure_tables_exist(conn, schema: str) -> None:
    schema = validate_identifier(schema)
    for table in ("media", "accounts"):
        result = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"{schema}.{table}"})
        if result.scalar() is None:
            raise RuntimeError(f"Missing table: {schema}.{table}. Run migrations first.")


def run_statements(conn, statements: list[str], dry_run: bool) -> None:
    for statement in statements:
        if dry_run:
            print(statement)
            print()
            continue
        conn.execute(text(statement))


def parse_creator_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    from config import parse_items_from_line, sanitize_creator_names

    return sorted(sanitize_creator_names(parse_items_from_line(raw)))


def parse_creator_id_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    mapping: dict[str, str] = {}
    for item in raw.replace(" ", "").split(","):
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid creator-id mapping: {item!r}")
        name, creator_id = item.split("=", 1)
        if not name or not creator_id:
            raise ValueError(f"Invalid creator-id mapping: {item!r}")
        mapping[name.lower()] = creator_id
    return mapping


def describe_files(base_path: Path) -> dict[str, int]:
    if not base_path.exists():
        return {"total": 0, "hash2": 0, "id_tag": 0}
    hash2_re = re.compile(r"_hash2_[a-fA-F0-9]+")
    id_re = re.compile(r"_id_\d+")
    total = 0
    hash2 = 0
    id_tag = 0
    for root, _dirs, files in os.walk(base_path):
        for name in files:
            total += 1
            if hash2_re.search(name):
                hash2 += 1
            if id_re.search(name):
                id_tag += 1
    return {"total": total, "hash2": hash2, "id_tag": id_tag}


def collect_hash2_entries(base_path: Path) -> list[dict[str, str | int | None]]:
    if not base_path.exists():
        return []
    hash2_re = re.compile(r"_hash2_([a-fA-F0-9]+)")
    id_re = re.compile(r"_id_(\d+)")
    entries: list[dict[str, str | int | None]] = []
    seen_ids: set[int] = set()
    for root, _dirs, files in os.walk(base_path):
        for name in files:
            hash_match = hash2_re.search(name)
            id_match = id_re.search(name)
            if not hash_match or not id_match:
                continue
            media_id = int(id_match.group(1))
            if media_id in seen_ids:
                continue
            seen_ids.add(media_id)
            mimetype, _ = mimetypes.guess_type(name)
            entries.append(
                {
                    "id": media_id,
                    "hash": hash_match.group(1),
                    "filename": name,
                    "mimetype": mimetype,
                }
            )
    return entries


def confirm_destructive(action: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to run destructive action without --yes.")
    prompt = f"{action} This cannot be undone. Continue? (y/N): "
    response = input(prompt).strip().lower()
    if response not in {"y", "yes"}:
        raise SystemExit("Aborted by user.")


def list_schema_tables(conn, schema: str) -> list[str]:
    schema = validate_identifier(schema)
    result = conn.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ),
        {"schema": schema},
    )
    return [row[0] for row in result.fetchall()]


def clean_database(conn, schema: str, dry_run: bool, assume_yes: bool) -> None:
    tables = [t for t in list_schema_tables(conn, schema) if t != "alembic_version"]
    if not tables:
        print("No tables to clean (excluding alembic_version).")
        return
    # Wipe data for a deterministic "fresh DB + existing files" bootstrap.
    confirm_destructive(
        f"Wiping all data from {schema} tables ({len(tables)} tables).",
        assume_yes,
    )
    schema_ident = f'"{validate_identifier(schema)}"'
    table_idents = []
    for table in tables:
        validate_identifier(table)
        table_idents.append(f"{schema_ident}.\"{table}\"")
    truncate_sql = (
        f"TRUNCATE TABLE {', '.join(table_idents)} RESTART IDENTITY CASCADE;"
    )
    run_statements(conn, [truncate_sql], dry_run=dry_run)


def ensure_media_locations_unique(conn, schema: str, dry_run: bool) -> None:
    schema = validate_identifier(schema)
    if conn.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{schema}.media_locations"}
    ).scalar() is None:
        print("media_locations table not found; skipping unique constraint check.")
        return

    # ON CONFLICT ("mediaId", "locationId") requires a unique/PK constraint.
    check_sql = text(
        """
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a1 ON a1.attrelid = t.oid AND a1.attname = 'mediaId'
        JOIN pg_attribute a2 ON a2.attrelid = t.oid AND a2.attname = 'locationId'
        WHERE n.nspname = :schema
          AND t.relname = 'media_locations'
          AND c.contype IN ('u', 'p')
          AND (
            c.conkey = ARRAY[a1.attnum, a2.attnum]
            OR c.conkey = ARRAY[a2.attnum, a1.attnum]
          )
        LIMIT 1
        """
    )
    exists = conn.execute(check_sql, {"schema": schema}).scalar() is not None
    if exists:
        return

    schema_ident = f'"{schema}"'
    add_sql = (
        f'ALTER TABLE {schema_ident}.media_locations '
        'ADD CONSTRAINT uq_media_locations UNIQUE ("mediaId", "locationId");'
    )
    run_statements(conn, [add_sql], dry_run=dry_run)


def ensure_media_locations_fk_deferrable(conn, schema: str, dry_run: bool) -> None:
    ensure_fk_deferrable(
        conn=conn,
        schema=schema,
        table="media_locations",
        column="mediaId",
        ref_table="media",
        ref_column="id",
        desired_name="fk_media_locations_media_id_deferrable",
        dry_run=dry_run,
    )


def ensure_fk_deferrable(
    *,
    conn,
    schema: str,
    table: str,
    column: str,
    ref_table: str,
    ref_column: str,
    desired_name: str,
    dry_run: bool,
) -> None:
    schema = validate_identifier(schema)
    for ident in (table, column, ref_table, ref_column):
        validate_identifier(ident)

    if conn.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{schema}.{table}"}
    ).scalar() is None:
        print(f"{table} table not found; skipping FK deferrable check.")
        return

    # Some inserts arrive out-of-order inside a single transaction.
    # Deferrable FKs allow the parent row to be flushed later before commit.
    result = conn.execute(
        text(
            """
            SELECT c.conname, c.condeferrable, c.condeferred
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class r ON r.oid = c.confrelid
            JOIN pg_namespace rn ON rn.oid = r.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attname = :column
            JOIN pg_attribute ar ON ar.attrelid = r.oid AND ar.attname = :ref_column
            WHERE n.nspname = :schema
              AND t.relname = :table
              AND c.contype = 'f'
              AND rn.nspname = :schema
              AND r.relname = :ref_table
              AND a.attnum = ANY (c.conkey)
              AND ar.attnum = ANY (c.confkey)
            LIMIT 1
            """
        ),
        {
            "schema": schema,
            "table": table,
            "column": column,
            "ref_table": ref_table,
            "ref_column": ref_column,
        },
    ).fetchone()

    schema_ident = f'"{schema}"'
    if result:
        constraint_name = result[0]
        is_deferrable = bool(result[1])
        is_deferred = bool(result[2])
        if is_deferrable and is_deferred:
            return
        drop_sql = (
            f'ALTER TABLE {schema_ident}."{table}" '
            f'DROP CONSTRAINT "{constraint_name}";'
        )
    else:
        drop_sql = None
        constraint_name = desired_name

    add_sql = (
        f'ALTER TABLE {schema_ident}."{table}" '
        f'ADD CONSTRAINT "{constraint_name}" FOREIGN KEY ("{column}") '
        f'REFERENCES {schema_ident}."{ref_table}" ("{ref_column}") '
        f'DEFERRABLE INITIALLY DEFERRED;'
    )
    statements = [s for s in [drop_sql, add_sql] if s]
    run_statements(conn, statements, dry_run=dry_run)


def build_config_for_dedupe(args: argparse.Namespace, settings: dict[str, str]):
    from config import FanslyConfig, load_config
    from fansly_downloader_ng import __version__
    from metadata import Database

    config = FanslyConfig(program_version=__version__)
    config.interactive = False
    load_config(config)

    # Keep DB settings aligned with CLI/env overrides used by this script.
    config.pg_host = settings["pg_host"]
    config.pg_port = int(settings["pg_port"])
    config.pg_database = settings["pg_database"]
    config.pg_user = settings["pg_user"]
    config.pg_password = settings["pg_password"]

    config._database = Database(config, creator_name=None)
    return config


async def resolve_creator_id(
    config,
    creator: str,
    creator_id_map: dict[str, str],
    allow_api: bool,
) -> str | None:
    mapped = creator_id_map.get(creator.lower())
    if mapped:
        return mapped

    # Try existing DB accounts (case-insensitive username match).
    try:
        from metadata.account import Account
        from sqlalchemy import func, select

        async with config._database.async_session_scope() as session:
            result = await session.execute(
                select(Account.id).where(func.lower(Account.username) == creator.lower())
            )
            account_id = result.scalar_one_or_none()
            if account_id is not None:
                return str(account_id)
    except Exception as exc:  # noqa: BLE001 - best-effort lookup only
        print(f"Account lookup failed for {creator}: {exc}")

    # Fall back to API lookup when token/config is present.
    if allow_api:
        try:
            from download.account import get_creator_account_info
            from download.downloadstate import DownloadState

            state = DownloadState(creator_name=creator)
            await get_creator_account_info(config, state)
            if state.creator_id:
                return str(state.creator_id)
        except Exception as exc:  # noqa: BLE001 - API lookup may fail offline
            print(f"API lookup failed for {creator}: {exc}")

    return None


async def run_dedupe_init(
    args: argparse.Namespace,
    settings: dict[str, str],
) -> None:
    from download.downloadstate import DownloadState
    from fileio.dedupe import dedupe_init
    from pathio import set_create_directory_for_download
    from metadata.media import Media

    config = build_config_for_dedupe(args, settings)

    creators = parse_creator_names(args.dedupe_creators)
    if not creators:
        creators = sorted(config.user_names or [])

    if not creators:
        print("No creators configured; skipping dedupe_init.")
        return

    creator_id_map = parse_creator_id_map(args.creator_ids)

    try:
        for creator in creators:
            creator_id = await resolve_creator_id(
                config, creator, creator_id_map, allow_api=not args.no_api
            )
            if not creator_id:
                print(f"Skipping {creator}: unable to resolve creator_id")
                continue

            state = DownloadState(creator_name=creator)
            state.creator_id = creator_id
            try:
                set_create_directory_for_download(config, state)
            except Exception as exc:
                print(f"Skipping {creator}: {exc}")
                continue

            counts = describe_files(state.download_path or Path("."))
            print(
                f"Dedupe init: {creator} ({creator_id}) -> {state.download_path} "
                f"(files={counts['total']}, hash2={counts['hash2']}, id_tag={counts['id_tag']})"
            )
            await dedupe_init(config, state)

            if args.fix_hash2_db and state.download_path:
                entries = collect_hash2_entries(state.download_path)
                if not entries:
                    continue
                async with config._database.async_session_scope() as session:
                    updated = 0
                    created = 0
                    for entry in entries:
                        media_id = entry["id"]
                        media = await session.get(Media, media_id)
                        if media is None:
                            media = Media(
                                id=media_id,
                                accountId=int(creator_id),
                                mimetype=entry["mimetype"],
                            )
                            session.add(media)
                            created += 1
                        media.content_hash = entry["hash"]
                        media.local_filename = entry["filename"]
                        media.is_downloaded = True
                        if not media.accountId:
                            media.accountId = int(creator_id)
                        updated += 1
                    await session.commit()
                print(
                    f"Hash2 DB fix: {creator} -> updated={updated}, created={created}"
                )
    finally:
        if getattr(config, "_database", None) is not None:
            config._database.close_sync()


def main() -> int:
    args = parse_args()
    if args.database_url:
        db_url = args.database_url
        settings = settings_from_db_url(db_url) or resolve_db_settings(args)
    else:
        settings = resolve_db_settings(args)
        db_url = build_db_url(settings)

    print_safe_db_url(db_url)

    statements = (
        sql_for_drop(args.schema)
        if args.drop
        else sql_for_create(args.schema, backfill=not args.no_backfill)
    )

    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            ensure_tables_exist(conn, args.schema)
            if not args.drop and args.clean:
                clean_database(conn, args.schema, dry_run=args.dry_run, assume_yes=args.yes)
            if not args.drop:
                # Fix constraints before the app writes any data.
                ensure_media_locations_unique(conn, args.schema, dry_run=args.dry_run)
                ensure_media_locations_fk_deferrable(conn, args.schema, dry_run=args.dry_run)
                ensure_fk_deferrable(
                    conn=conn,
                    schema=args.schema,
                    table="account_media_bundle_media",
                    column="media_id",
                    ref_table="account_media",
                    ref_column="id",
                    desired_name="fk_account_media_bundle_media_media_deferrable",
                    dry_run=args.dry_run,
                )
                ensure_fk_deferrable(
                    conn=conn,
                    schema=args.schema,
                    table="account_media_bundle_media",
                    column="bundle_id",
                    ref_table="account_media_bundles",
                    ref_column="id",
                    desired_name="fk_account_media_bundle_media_bundle_deferrable",
                    dry_run=args.dry_run,
                )
            run_statements(conn, statements, dry_run=args.dry_run)
    finally:
        engine.dispose()

    action = "Dropped" if args.drop else "Created"
    print(f"{action} media stub trigger successfully.")

    if args.print_creator_ids and not args.dry_run:
        async def _print_ids() -> None:
            config = build_config_for_dedupe(args, settings)
            try:
                creators = parse_creator_names(args.dedupe_creators)
                if not creators:
                    creators = sorted(config.user_names or [])
                if not creators:
                    print("No creators configured.")
                    return
                creator_id_map = parse_creator_id_map(args.creator_ids)
                pairs = []
                for creator in creators:
                    creator_id = await resolve_creator_id(
                        config, creator, creator_id_map, allow_api=not args.no_api
                    )
                    if creator_id:
                        pairs.append(f"{creator}={creator_id}")
                if pairs:
                    print("Creator IDs:", ", ".join(pairs))
            finally:
                if getattr(config, "_database", None) is not None:
                    config._database.close_sync()

        asyncio.run(_print_ids())
        return 0

    if args.run_dedupe_init and not args.drop and not args.dry_run:
        asyncio.run(run_dedupe_init(args, settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
