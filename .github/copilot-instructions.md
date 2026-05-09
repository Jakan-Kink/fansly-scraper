# Fansly Downloader NG — Copilot Guidelines

## Project Overview

Content downloader for Fansly. Key modules: `config/`, `api/`, `download/`, `metadata/`, `daemon/`, `stash/`.

Full architecture: `docs/reference/architecture.md`. Monitoring cadences: `docs/reference/monitoring-cadence.md`.

## Hard Constraints

- **NEVER** edit `poetry.toml` directly — use `poetry add` / `poetry remove`
- **NEVER** run modifying git commands: `git commit`, `git add`, `git stash`, `git push --force`, `git reset --hard`, `git rebase`
- **NEVER** use CLI tools to edit files: `sed`, `awk`, inline `python`, `find -exec` — use editor tools instead
- **ALWAYS** use `rm -f` (not bare `rm`)

## Architecture

### Key Design Patterns

**Pydantic + asyncpg EntityStore** (canonical for all metadata work):

```python
store = get_store()
obj = Model.model_validate(data)       # identity map dedup, auto-coerces str IDs → int, int timestamps → datetime
await store.save(obj)                  # INSERT if _is_new, UPDATE dirty fields only
```

EntityStore filter syntax (mirrors stash-graphql-client):

```python
store.find(Media, is_downloaded=True)          # EQUALS
store.find(Media, mimetype__contains="video")  # ILIKE
store.find(Media, duration__gte=100)           # >=
store.find(Post, id__in=[1, 2, 3])            # = ANY(...)
store.find(Media, content_hash__null=True)     # IS NULL
```

**Mixin Pattern** (`StashProcessing`): `StashProcessingBase` + domain mixins in `processing/mixins/` → final `StashProcessing`.

**Daemon**: WebSocket events + timeline/story polling with `ActivitySimulator` (active → idle → hidden state machine).

### Adding Metadata Models

1. Pydantic model in `metadata/models.py`
2. Table in `metadata/tables.py` (Alembic only)
3. Register in `_TYPE_REGISTRY` in `metadata/entity_store.py`
4. `alembic revision --autogenerate -m "..."`
5. Factory in `tests/fixtures/metadata/metadata_factories.py`

## Build and Test

```bash
pytest -n8 -rs                          # all tests with coverage
ruff check . --fix && ruff format .
mypy .
bandit -c pyproject.toml -r .
```

## Testing Requirements

**Mock only at edges** — everything internal runs real:

| Mock (respx) | Run Real |
|---|---|
| Fansly API HTTP | Database (`uuid_test_db_factory`) |
| Stash GraphQL HTTP | Config, print/logging, sleep/timing |
| Leaf calls: `imagehash.phash`, `hashlib`, ffmpeg subprocess | Dedupe, metadata processing, internal wrappers |

**Fixture location**: All fixtures, fakes, factories in `tests/fixtures/` — never inline in `test_*.py`.

- Factories: `tests/fixtures/metadata/metadata_factories.py`
- Full rules: `docs/testing/TESTING_REQUIREMENTS.md`
- Mocking-boundary table: `tests-to-100/CLAUDE.md`

## Code Style

- Line length: 88 (Ruff/Black)
- Type hints: PEP 604 (`str | None`, not `Optional[str]`); lowercase built-ins (`dict`, `list`)
- Imports: PEP 8, at file top — **never inline inside functions, fixtures, or test bodies**
- Async functions: prefix `async_` (e.g., `async_process_story`), only when there is also a sync version.
- Prefer F-strings for string formatting, but str.format and {},var are allowed in complex situations or loguru logging.
- McCabe complexity target ≤ 12; hard max 14

## Git — Ignore These in Status

`config.yaml`, `sqlalchemy.log.*.gz`, `.DS_Store`, `cov.xml`, `.coverage`, `htmlcov/`
