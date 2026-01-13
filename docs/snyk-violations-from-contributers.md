# Snyk violations by contributor PR (derived)

## PR #18 – Fix dedupe filename handling, legacy hash2 migration, bundle hardening

- Added `db_init_scratch.py` and `migrate_from_ng.py`; Snyk flags medium path-traversal risks in `db_init_scratch.py` (CLI args flow into `os.walk`/`Path` without sanitization). Not addressed in this batch.
- No code we touched today came from this PR.

## Current Snyk scan (Jan 12, 2026)

- Open issues (selection):
  - Low: “Use of Hardcoded Credentials” in tests (`tests/stash/processing/unit/gallery/test_gallery_creation.py`, `test_base.py`, `test_gallery_methods.py`, `test_studio_mixin.py`).
  - Medium: Path traversal in `db_init_scratch.py`, `scripts/migrate_to_postgres.py`; potential `os.utime`/path misuse flagged in older `download/single.py` (not reproducible in current file contents).
  - Medium: SQL injection warnings in `config/browser.py`, `scripts/migrate_to_postgres.py`, `db_init_scratch.py`.

## Remediations applied now

- Replaced hardcoded usernames/strings in tests with faker-generated values to avoid false-positive credential flags:
  - `tests/stash/processing/unit/test_base.py`
  - `tests/stash/processing/unit/gallery/test_gallery_creation.py`
  - `tests/stash/processing/unit/test_gallery_methods.py`
  - `tests/stash/processing/unit/test_studio_mixin.py`

## Outstanding (not addressed in this batch)

- Path traversal & SQL injection warnings in `db_init_scratch.py` and `scripts/migrate_to_postgres.py` (per request, left untouched).
- Verify if Snyk still flags `download/single.py` in future scans; current file has no `os.utime` or path writes.
