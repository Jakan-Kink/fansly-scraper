# Monitoring Daemon Architecture Plan

Based on reverse-engineering Fansly's `main.js` frontend, live WebSocket traffic analysis,
and overnight monitoring tests. All intervals, endpoints, and behaviors are confirmed
against the actual production code.

## Agreed Architecture Decisions (2026-04-16)

These decisions are the implementation contract; subsequent sections describe
the *behaviors*, this section pins down the *shape*.

### Invocation model — flag, not mode

The daemon is **not** a `DownloadMode`. It is a flag that appends a monitoring
phase after a normal batch download completes. This lets `-dm normal -u alice
--daemon` mean "download everything once, then watch forever."

| Flag | Alias | Alias | Behavior |
|---|---|---|---|
| `-d` | `--daemon` | `--monitor` | Enter daemon loop after the batch download completes. Runs until SIGINT. |

Empirically verified: `-d` coexists with existing `-dir` and `-dm` because
argparse matches option strings exactly (tested 2026-04-16).

### Creator scope — respects existing flags

- `-u alice,bob` → daemon monitors only alice and bob
- `-uf` / `-ufp` → daemon monitors the full following list
- The following list is **refreshed** on `svc=15 type=5 status=3`
  (subscription confirmed) events so newly-subscribed creators join the
  watch set mid-run without a restart

### Package layout — new `daemon/` top-level package

```
daemon/
├── __init__.py          # public API: run_daemon()
├── runner.py            # orchestrator loop; owns FanslyWebSocket + ActivitySimulator + queue
├── simulator.py         # ActivitySimulator (three-tier state machine)
├── polling.py           # poll_home_timeline(), poll_story_states()
├── filters.py           # should_process_creator() — lastSeenAt short-circuit
├── handlers.py          # WS event → work-item translators
└── state.py             # MonitorState persistence helpers
```

`daemon/` parallels `download/`, `metadata/`, `api/`, `config/`, etc.

### Event routing — `register_handler`, not `monitor_events`

`FanslyWebSocket.monitor_events=True` is for human-readable protocol
discovery (the `scripts/ws_monitor.py` tool). The daemon instead calls
`ws.register_handler(MSG_SERVICE_EVENT, daemon_dispatcher)` to receive
decoded events and act on them. These two mechanisms must not be conflated.

### Persistent state — `MonitorState` table

Scoped schema — the identity map already tracks `Post` existence via
`store.get_from_cache(Post, id)` (see `download/common.py::check_page_duplicates`),
so **no `lastPostIdSeen` column** is needed. The table stores only state that
has no existing home:

| Column | Type | Purpose |
|---|---|---|
| `creatorId` | `BigInteger` PK | The creator this state is for |
| `lastHasActiveStories` | `Boolean` | Prior value of `hasActiveStories` (drives flip detection) |
| `lastSeenAtAtLastRun` | `DateTime` | Snapshot of `Account.lastSeenAt` at last daemon tick (Optimization 3) |
| `lastRunAt` | `DateTime` | When daemon last processed this creator |
| `updatedAt` | `DateTime` | Row modification time |

Persistence also means a daemon restart doesn't re-trigger every story or
re-scan the full `check_page_duplicates` sequence against a cold cache.

### Test strategy — TDD with full coverage

Target ≥ 90% coverage for `daemon/` module per RULE 0 and the
`tests-to-100` effort. Test layout follows the existing convention:

```
tests/daemon/
├── unit/
│   ├── test_simulator.py            # ActivitySimulator state transitions (inject time)
│   ├── test_polling.py              # poll_home_timeline / poll_story_states (RESPX)
│   ├── test_filters.py              # should_process_creator (real EntityStore + factories)
│   ├── test_handlers.py             # WS event → work-item translation (decoded dicts)
│   ├── test_monitor_state.py        # MonitorState model + EntityStore round-trips
│   └── test_runner_wiring.py        # simulator/handler/queue interactions
└── integration/
    └── test_runner_e2e.py           # Full daemon loop: RESPX + injected WS events + real DB
```

Plus updates to existing tests:
- `tests/api/` — new tests for `get_home_timeline`, `get_story_states_following`
- `tests/download/unit/test_stories.py` — cover `mark_viewed=False` branch
- `tests/fixtures/metadata/metadata_factories.py` — `MonitorStateFactory`
- `tests/conftest.py` — possibly a `daemon_sandbox` fixture

### File map (implementation scope)

New files:
- `daemon/__init__.py`, `daemon/runner.py`, `daemon/simulator.py`,
  `daemon/polling.py`, `daemon/filters.py`, `daemon/handlers.py`,
  `daemon/state.py`
- `alembic/versions/<id>_add_monitor_state.py`
- `tests/daemon/__init__.py`, `tests/daemon/unit/__init__.py`,
  `tests/daemon/integration/__init__.py`, plus 7 test files listed above

Modified files:
- `api/fansly.py` — two new methods
- `download/stories.py` — `mark_viewed` param
- `config/args.py` — `-d`/`--daemon`/`--monitor` flag + `[Monitoring]` CLI overrides
- `config/fanslyconfig.py` — `daemon_mode`, daemon duration fields
- `config/validation.py` — daemon flag validation
- `config/config.ini.example` (or equivalent) — `[Monitoring]` section
- `fansly_downloader_ng.py` — post-batch daemon hook
- `metadata/models.py` — `MonitorState` Pydantic model
- `metadata/tables.py` — `MonitorState` Table
- `metadata/entity_store.py` — `_TYPE_REGISTRY` entry
- `metadata/__init__.py` — re-export `MonitorState`
- `tests/fixtures/metadata/metadata_factories.py` — `MonitorStateFactory`
- `tests/api/` — two new test modules (or additions to existing)
- `tests/download/unit/test_stories.py` — new branch coverage

Expected total: ~25 files touched (10+ new production, 10+ new tests,
several modified). The plan is intentionally multi-commit within the
`feat/websocket-monitoring` branch.

## Spike: config.ini → YAML migration (in-scope for this branch)

### Why fold this in now

Adding a `[Monitoring]` section to `config.ini` with ~8 new keys (durations,
intervals, enable flags, WS event interrupt list) would compound an existing
problem: the current config has ~131 individual `configparser._parser.get*()`
call sites with string→bool/int coercion scattered across `config/config.py`
and `config/fanslyconfig.py`. Adding a whole subsystem's worth of options on
top of that coercion soup is technical debt we don't want to ship. Converting
to YAML before adding monitoring keys gives us:

- Native typed values (booleans, ints, lists, nested maps) — no `getboolean`/`getint` ceremony
- Nested structure — `monitoring.activity.active_duration` instead of `[Monitoring] active_duration`
- Lists as first-class (the `INTERRUPT_EVENTS` set, the creator scope list when `-u` isn't passed)
- Round-trip-safe comments for the user-facing config file

### Library choice — `ruamel.yaml`

**`ruamel.yaml`** is actively maintained (0.18.x series, regular PyPI
releases since 2014). It's YAML 1.2 compliant (PyYAML is still YAML 1.1,
with quirks like `yes`/`no`/`on`/`off` parsed as booleans). Most important
for this project: ruamel preserves comments, key order, and whitespace on
load → modify → dump cycles.

The app **writes config back** on first-run setup (captures token, user
agent, device ID from the browser). PyYAML destroys user comments and
reorders keys on dump — not acceptable for a human-edited file that gets
mutated.

`pydantic-yaml` is the wrong tool for this job: its comments are generated
from the Pydantic schema model (`Field(description=…)` / class docstrings),
NOT from the user's YAML file. Every writeback would replace the user's
hand-edited comments with schema-defined ones.

Decision: pull `ruamel.yaml` only. Load → dict → `ConfigSchema.model_validate()`
on read. `schema.model_dump(mode="python")` → ruamel dump on write. The
initial `config.sample.yaml` is hand-authored with section-level comments
so fresh installs still get a well-documented file.

```bash
poetry add ruamel.yaml
```

### Schema — Pydantic model layer

The project already uses Pydantic for its metadata models. Replace the
scattered `_parser.getX(section, key, fallback=…)` pattern with a single
Pydantic `ConfigSchema` that loads from YAML once and exposes typed
attributes:

```python
# config/schema.py (new)
class MonitoringSection(BaseModel):
    enabled: bool = False
    active_duration_minutes: int = 60
    idle_duration_minutes: int = 120
    hidden_duration_minutes: int = 300
    timeline_poll_active_seconds: int = 180
    timeline_poll_idle_seconds: int = 600
    story_poll_active_seconds: int = 30
    story_poll_idle_seconds: int = 300
    interrupt_events: list[tuple[int, int]] = Field(
        default_factory=lambda: [(5, 1), (15, 5), (2, 7), (2, 8)]
    )

class ConfigSchema(BaseModel):
    targeted_creator: TargetedCreatorSection
    my_account: MyAccountSection
    options: OptionsSection
    postgres: PostgresSection
    monitoring: MonitoringSection = Field(default_factory=MonitoringSection)
    logic: LogicSection
```

`FanslyConfig.from_yaml(path)` constructs the schema, then copies attributes
onto `FanslyConfig` using the existing attribute names (for transition
simplicity — call sites keep working). Later passes can migrate call sites
to read `config.schema.monitoring.active_duration_minutes` directly.

### Retire `config_args.ini` in the same pass

`config_args.ini` is a workaround from the pre-fork original design. When
CLI args override config values, the app swaps `config.config_path` from
`config.ini` to a temporary `config_args.ini`, then calls
`save_config_or_raise()` so that CLI overrides get written to the throwaway
file instead of clobbering the user's authoritative `config.ini`
(see `config/args.py:816-820`, `RewriteNotes.md:23`). Token writebacks
route through `_save_token_to_original_config` using the preserved
`original_config_path`.

The whole dance exists because **"in-memory state" and "disk state" share
one mutable object** — any `save_config_or_raise()` necessarily hits disk,
so CLI overrides had to be misdirected to a decoy file to keep them out of
the real config.

The YAML migration is the natural moment to retire this. The schema-driven
architecture models the two layers explicitly:

```python
# Load once from disk — treated as read-only for the run
disk_schema = ConfigSchema.load_yaml("config.yaml")

# CLI overlays live only in-memory; never written back
runtime_schema = disk_schema.model_copy(update=cli_overrides, deep=True)

# Legitimate writebacks (fresh token from browser login) mutate disk_schema
# targeted at the file, with no CLI overlay involvement:
if token_captured_this_run:
    # Re-read in case of external edits, patch the single field, write
    disk_schema = ConfigSchema.load_yaml("config.yaml")
    disk_schema.my_account.token = new_token
    disk_schema.dump_yaml("config.yaml")
```

Result: no temp file, no `original_config_path`, no `_save_token_to_original_config`
/ `_save_checkkey_to_original_config` helpers. CLI args behave like CLI args
in every other well-designed Python tool — they shape the run, they don't
touch disk.

Files to remove/simplify:
- `config_args.ini` entry in `.gitignore`
- `config.original_config_path` field in `FanslyConfig`
- `_save_token_to_original_config`, `_save_checkkey_to_original_config` helpers
- The path-swap block in `config/args.py:816-820` and its callers

### Migration path for existing users

Existing installations have `config.ini`. A hard cutover would break
automation. The migration is one-shot and silent:

1. On startup, `config/loader.py` checks for `config.yaml` first
2. If `config.yaml` missing and `config.ini` exists:
   - Read `config.ini` via existing `ConfigParser` logic
   - Project onto `ConfigSchema`
   - Dump to `config.yaml` with section-level comments
   - Rename `config.ini` → `config.ini.bak.<timestamp>`
   - Log a one-liner telling the user what happened
3. Subsequent runs use `config.yaml` only

Writeback (`save_config_or_raise`) becomes `schema.dump(path)` — a single
ruamel call instead of the current 30+ `_parser.set()` calls.

### Tactical note — do the conversion FIRST, then add monitoring keys

Order of operations within this branch:

1. Land YAML conversion as a standalone commit (no behavior change — every
   existing key reads the same value from `config.yaml` as it did from
   `config.ini`)
2. Add the `[Monitoring]` → `monitoring:` section in a second commit
3. Daemon work (tasks #2-#12) builds on top

This keeps diffs reviewable: conversion and new behavior aren't tangled in
the same commit.

### File map additions for the YAML spike

New files:
- `config/schema.py` — Pydantic `ConfigSchema` + all section models
- `config/loader.py` — YAML load + one-shot `.ini` migration
- `config/writer.py` — round-trip YAML dump (or fold into loader)
- `config.sample.yaml` — template replacing `config.sample.ini`
- `tests/config/unit/test_schema.py` — Pydantic validation, defaults
- `tests/config/unit/test_loader.py` — YAML load, missing-file handling
- `tests/config/integration/test_ini_migration.py` — end-to-end `.ini` → `.yaml`
  migration with real files (comment preservation, backup creation, value parity)

Modified files:
- `pyproject.toml` / `poetry.lock` — add `ruamel.yaml`
- `config/config.py` — replace ~60 `_parser.*` calls with schema reads
- `config/fanslyconfig.py` — replace ~71 `_parser.*` calls; drop `_parser` field
- `config/validation.py` — keep validation logic, point it at schema
- `config/__init__.py` — re-export `ConfigSchema`
- `fansly_downloader_ng.py` — startup: prefer YAML, one-shot migrate if only `.ini` present
- `.gitignore` — add `config.yaml`, `config.ini.bak.*`

Rough size: 10+ new files, ~8 modifications. This spike alone is comparable
in scope to the daemon itself — the user expectation of "much more than 7
files" applies squarely here.

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| User has a heavily-customized `config.ini` → migration silently loses something | Dump YAML, then re-parse it into `ConfigSchema`, and compare to the in-memory state loaded from `.ini`. On any mismatch, abort migration and print a diff. |
| Automation runs overlap the migration (two processes see `.ini`, both try to write `.yaml`) | Acquire `config.ini.migrating.lock` via exclusive file open before migrating |
| Pydantic validation rejects edge cases present in prod `.ini` files (blank strings, unusual types) | First migration uses `BaseModel` with permissive validators; tighten in a follow-up |
| Comment loss / reorder on writeback despite `ruamel.yaml` | Integration test asserts comment preservation across a full load→modify→dump→reload cycle |

## Event Detection Architecture

### What the WebSocket CAN detect (account-scoped, real-time)

| Event | Service | Type | Action |
|---|---|---|---|
| New DM received | MessageService (5) | 1 | Download message + attachments for that group |
| Message with attachment | MessageService (5) | 1 | Check `attachments[]` for contentType 1/2, download media |
| Message deleted | MessageService (5) | 10 | Mark media as removed (optional) |
| New subscription confirmed | SubscriptionService (15) | 5 (status=3) | Queue full download for newly-accessible creator |
| PPV media purchased | MediaService (2) | 7 | Re-download that creator's media (now accessible) |
| PPV bundle purchased | MediaService (2) | 8 | Re-download that creator's bundles |
| New follow | FollowerService (3) | 2 | Permission flag 2 added — some content may be newly accessible |
| Wallet credited | WalletService (6) | 2 | Informational — balance available for purchases |

### What the WebSocket CANNOT detect (requires polling)

| Content | Why | Detection method |
|---|---|---|
| New posts from creators | Server doesn't push followed creators' posts | Home timeline poll |
| New stories from creators | Server doesn't push story state changes | Story state poll |
| New wall posts | Only pushed for YOUR walls via PostService type=9 | Home timeline poll |
| Creator profile changes | No push for followed accounts | Following list refresh |

### Polling Cadence (matching real browser behavior)

```
┌─────────────────────────────────────────────────────────────────┐
│                    MONITORING DAEMON LOOP                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  WebSocket (persistent)                                          │
│  ├── Ping every 20-25s (jittered)                               │
│  ├── Message events → immediate download                         │
│  ├── Subscription events → queue creator download                │
│  └── PPV purchase events → re-download creator media             │
│                                                                  │
│  Story State Poll (every 30s active / 5min idle)                 │
│  ├── GET /mediastories/following?limit=100&offset=0              │
│  ├── Compare hasActiveStories against last known state           │
│  └── If flipped true → download_stories(creator)                 │
│                                                                  │
│  Home Timeline Poll (every 3min + jitter active / 10min idle)    │
│  ├── GET /timeline/home?before=0&after=0&mode=0                  │
│  ├── Compare post IDs against last-seen set                      │
│  ├── Identify which creators have new posts                      │
│  └── For each new-content creator → download_timeline(creator)   │
│                                                                  │
│  Anti-Detection: simulate idle periods                           │
│  ├── After 30min of no new content: switch to idle intervals     │
│  ├── After 3hr: pause all polling for 5-10min (tab-hidden sim)   │
│  └── On resume: assertWebsocketConnection() first, then poll     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Optimization 1: Home Timeline Diffing

Instead of downloading every creator's timeline on every poll, use the home
timeline to identify WHICH creators posted new content.

```python
async def poll_home_timeline(config, last_seen_post_ids: set) -> set[int]:
    """Poll home timeline, return set of creator IDs with new posts."""
    response = config.get_api().get_home_timeline()
    data = config.get_api().get_json_response_contents(response)

    new_creator_ids = set()
    new_post_ids = set()

    for post in data.get("posts", []):
        post_id = post["id"]
        if post_id not in last_seen_post_ids:
            new_creator_ids.add(post["accountId"])
            new_post_ids.add(post_id)

    last_seen_post_ids.update(new_post_ids)
    return new_creator_ids
```

One API call detects new content across ALL followed creators. Only creators
with new posts need their timeline downloaded.

### API method needed

```python
def get_home_timeline(self) -> httpx.Response:
    """Fetch home timeline for all followed creators."""
    return self.get_with_ngsw(
        url="https://apiv3.fansly.com/api/v1/timeline/home",
        params={"before": "0", "after": "0", "mode": "0"},
    )
```

## Optimization 2: Story State Diffing

```python
async def poll_story_states(config, last_story_states: dict) -> list[int]:
    """Poll story states, return creator IDs with new active stories."""
    response = config.get_api().get_story_states_following()
    states = config.get_api().get_json_response_contents(response)

    creators_with_new_stories = []
    for state in states:
        account_id = state["accountId"]
        was_active = last_story_states.get(account_id, {}).get("hasActiveStories", False)
        is_active = state.get("hasActiveStories", False) or state.get("storyCount", 0) > 0

        if is_active and not was_active:
            creators_with_new_stories.append(account_id)

        last_story_states[account_id] = state

    return creators_with_new_stories
```

### API method needed

```python
def get_story_states_following(self) -> httpx.Response:
    """Fetch story states for all followed creators."""
    return self.get_with_ngsw(
        url="https://apiv3.fansly.com/api/v1/mediastories/following",
        params={"limit": "100", "offset": "0"},
    )
```

## Optimization 3: Timeline Post-Timestamp Skip

### Background — signals we investigated and rejected

The original plan proposed skipping creators whose `Account.lastSeenAt`
hadn't advanced since the last run. Two alternative signals were
considered. All three had disqualifying flaws:

| Signal | Source | Why it fails as a skip criterion |
|---|---|---|
| `Account.lastSeenAt` | Fansly API presence field | **Privacy opt-out returns 0.** main.js line 16014: `t.lastSeenAt = e.lastSeenAt \|\| 0`. Creators with last-seen visibility off return `0` forever → compared as 1970-01-01 → stuck-skipped indefinitely. |
| `TimelineStats.fetchedAt` | Timeline stats metadata | **Not a content-change timestamp.** Empirical evidence (user observation): `fetchedAt = 1774849061652` (ms, = 2026-04-27) appeared ~18 h AFTER the creator's most recent `createdAt` (1774783936 s, = 2026-04-26) with no new content in between. main.js evidence: client NEVER reads `timelineStats.fetchedAt` for any purpose; it's server cache-regeneration metadata, not post-event metadata. |
| Fansly `lastPostedAt` equivalent | N/A | **Does not exist.** Zero hits for `lastPosted`, `latestPost`, `postCount`, `mostRecent` in main.js. If Fansly tracked "when did this creator last post?" anywhere, the client UI would surface it — it doesn't. |

The only safe "did the creator post new content since we last checked?"
signal Fansly exposes is the actual `post.createdAt` value on each
timeline entry.

### Design: first-page timeline probe

```python
async def should_process_creator(
    config: FanslyConfig,
    creator_id: int,
    *,
    session_baseline: datetime | None = None,
) -> bool:
    """Skip creators whose most recent non-pinned post is older than the
    effective baseline.

    Baseline resolution:
      effective = session_baseline
                  if session_baseline is not None
                  else MonitorState.lastCheckedAt
      If effective is None → True (creator has never been processed).

    Fetches the creator's first timeline page (one API call). Filters
    pinned posts — creators can re-pin old content at any time, so a
    pinned post's timestamp tells us nothing about new activity. Returns
    True on API failure (err toward processing) and False when the
    timeline contains only pinned or no posts.
    """
```

Paired with:

```python
async def mark_creator_processed(creator_id: int) -> None:
    """Set MonitorState.lastCheckedAt = datetime.now(UTC). Creates the
    MonitorState row if absent (no-ops if Account FK doesn't yet exist)."""
```

### MonitorState column: `lastCheckedAt`

Renamed from `lastSeenAtAtLastRun` (which meant "snapshot of the
creator's Account.lastSeenAt at our last run") to `lastCheckedAt`
("wall-clock time when we last verified this creator had no new
content"). Semantically cleaner — this is about OUR poll cadence, not a
snapshot of a Fansly field.

### Session baseline override

CLI and config can supply a per-run override that ignores stored
`MonitorState.lastCheckedAt` values:

| Flag | Equivalent | Use case |
|---|---|---|
| `--monitor-since 2026-01-01T00:00:00Z` | `session_baseline=<iso>` | Cherry-pick a precise re-sync window (e.g., after a known-bad period) |
| `--full-pass` | `session_baseline=2000-01-01T00:00:00Z` | Zero-friction "download everything that's new since epoch" |

Mutually exclusive at CLI. Persisted via `[Monitoring] session_baseline`
in YAML (null by default). Session baseline takes precedence over
per-creator `lastCheckedAt` when non-None.

### Cold-start semantics

- Fresh install: no `MonitorState` rows → `should_process_creator` returns
  True for every creator → first pass downloads everything available.
- Existing install with baseline override: `--full-pass` effectively
  resets to "re-check every creator's posts since year 2000."
- Existing install without override: each creator checked against own
  `lastCheckedAt`; skipped if nothing new since.

### Prefetch-as-filter optimization (future)

`should_process_creator` already fetches the first timeline page. When
it returns True, `download_timeline` will re-fetch the same page. A
simple in-memory cache passed via state could eliminate the duplicate
call. Not implemented in the initial daemon runner — noted as a
follow-up for Task #10.

### What this optimization does NOT guarantee

If Fansly's first timeline page returns stale cached data (CDN
lag, server-side delay), we might skip a creator who just posted. The
home-timeline poll + WS subscription events are the stronger signals;
Optimization 3 is a cheap pre-filter layered on top.

## Important: Story View Tracking

The monitoring daemon should NOT call `mark_story_viewed()` when downloading
stories. Marking stories as viewed would affect the user's real Fansly
experience — they'd see story rings as "already watched" in the browser.

`mark_story_viewed()` should only be called in explicit download mode
(`-dm stories` or as part of `-dm normal`), not in the monitoring daemon.
The `download_stories()` function needs a `mark_viewed: bool = True` parameter
that the daemon sets to `False`.

## Optimization 4: WebSocket-Triggered Downloads

### Message download on WS event

```python
async def on_new_message(event_data: dict):
    """WebSocket svc=5 type=1 handler — download message attachments."""
    message = event_data.get("message", {})
    group_id = message.get("groupId")
    attachments = message.get("attachments", [])

    if attachments:
        # Has media — trigger message download for this group
        await download_messages_for_group(config, state, group_id)
```

### Subscription-triggered full download

```python
async def on_subscription_confirmed(event_data: dict):
    """WebSocket svc=15 type=5 status=3 handler — new creator accessible."""
    subscription = event_data.get("subscription", {})
    if subscription.get("status") != 3:
        return  # Only act on confirmed (status=3), not pending (status=2)

    creator_id = subscription.get("accountId")
    # Queue full download for this creator — first time access
    await download_timeline(config, state)
    await download_stories(config, state)
    await download_messages(config, state)
    await download_wall(config, state)
```

### PPV purchase re-download

```python
async def on_ppv_purchase(event_data: dict):
    """WebSocket svc=2 type=7/8 handler — media now accessible."""
    order = event_data.get("order", {})
    creator_id = order.get("correlationAccountId")
    # PPV content appears in both timelines AND messages
    # Re-process both paths for this creator
    await download_timeline(config, state)
    await download_messages(config, state)
```

## Optimization 5: Three-Tier Activity Simulation

The daemon cycles through three states to mimic real browser behavior.
All durations are configurable via CLI args and config.ini.

```
┌──────────┐     after active_duration      ┌──────────┐     after idle_duration      ┌──────────┐
│  ACTIVE  │ ──────────────────────────────→ │   IDLE   │ ──────────────────────────→  │  HIDDEN  │
│ (3min TL)│                                 │(10min TL)│                               │ (no poll)│
│ (30s ST) │                                 │ (5min ST)│                               │(WS pings)│
└──────────┘                                 └──────────┘                               └──────────┘
      ↑                                                                                      │
      └──────────────────────── after hidden_duration ────────────────────────────────────────┘
                              (assertWebsocketConnection first)
```

### Config settings (CLI + config.ini)

```ini
[Monitoring]
# Duration of each activity state (minutes)
active_duration = 60        # Time in "active" mode (frequent polling)
idle_duration = 120         # Time in "idle" mode (reduced polling)
hidden_duration = 300       # Time in "hidden" mode (no polling, WS only)
```

```python
class ActivitySimulator:
    """Three-tier activity simulation matching real browser behavior."""

    def __init__(self, active_min=60, idle_min=120, hidden_min=300):
        self.active_duration = active_min * 60
        self.idle_duration = idle_min * 60
        self.hidden_duration = hidden_min * 60
        self.state = "active"
        self.state_entered_at = time.time()
        self.last_new_content = time.time()

    @property
    def timeline_interval(self) -> float:
        if self.state == "active":
            return 180 + random.uniform(0, 10)  # 3 min + jitter
        if self.state == "idle":
            return 600 + random.uniform(0, 10)  # 10 min + jitter
        return 0  # hidden — no polling

    @property
    def story_interval(self) -> float:
        if self.state == "active":
            return 30
        if self.state == "idle":
            return 300
        return 0  # hidden — no polling

    @property
    def should_poll(self) -> bool:
        return self.state != "hidden"

    def on_new_content(self):
        """Reset to active when new content is discovered."""
        self.last_new_content = time.time()
        if self.state != "active":
            self.state = "active"
            self.state_entered_at = time.time()

    def tick(self) -> str | None:
        """Check for state transition. Returns new state or None."""
        elapsed = time.time() - self.state_entered_at
        old_state = self.state

        if self.state == "active" and elapsed > self.active_duration:
            self.state = "idle"
            self.state_entered_at = time.time()
        elif self.state == "idle" and elapsed > self.idle_duration:
            self.state = "hidden"
            self.state_entered_at = time.time()
        elif self.state == "hidden" and elapsed > self.hidden_duration:
            self.state = "active"
            self.state_entered_at = time.time()
            return "unhide"  # Signal to reconnect WS first

        return self.state if self.state != old_state else None
```

### On state transitions

- **active → idle**: Reduce polling frequency, keep everything running
- **idle → hidden**: Stop ALL polling, keep WS pings alive (real browser behavior)
- **hidden → active**: Call `assertWebsocketConnection()` first, brief delay, then resume polling
- **Any → active**: New content detected resets to active regardless of current state

### WS events interrupt hidden state

The hidden duration is a MAX, not a fixed sleep. WS events still arrive
during hidden state (pings keep the connection alive). Certain events
should break out of hidden immediately:

```python
# Events that interrupt hidden → active
INTERRUPT_EVENTS = {
    (5, 1),   # New message — someone sent a DM
    (15, 5),  # Subscription confirmed — new content accessible
    (2, 7),   # PPV purchased — locked content now available
    (2, 8),   # PPV bundle purchased
}

async def on_ws_event_during_hidden(service_id, event_type, simulator):
    """Check if a WS event should wake from hidden state."""
    if simulator.state == "hidden" and (service_id, event_type) in INTERRUPT_EVENTS:
        simulator.state = "active"
        simulator.state_entered_at = time.time()
        # assertWebsocketConnection already live — just resume polling
```

This matches real browser behavior: a desktop notification (new message)
causes the user to switch back to the Fansly tab, resuming all activity.

## Complete Event-to-Action Map

| Source | Event | Download Action |
|---|---|---|
| Home timeline poll | New post ID detected | `download_timeline(creator)` |
| Story state poll | `hasActiveStories` flipped true | `download_stories(creator)` |
| WS svc=5 type=1 | New message with attachments | `download_messages(group)` |
| WS svc=15 type=5 status=3 | Subscription confirmed | Full download for creator |
| WS svc=2 type=7 | PPV media purchased | Re-download creator media |
| WS svc=2 type=8 | PPV bundle purchased | Re-download creator bundles |
| WS svc=3 type=2 | New follow | Check for newly-accessible content |
| WS svc=6 type=2 | Wallet credited | Informational only |

## API Calls Per Cycle (optimized)

| Phase | API Calls | Frequency |
|---|---|---|
| Story state check | 1 call | Every 30s-5min |
| Home timeline check | 1 call | Every 3-10min |
| Per-creator timeline download | N calls (only creators with new posts) | On demand |
| Per-creator story download | N calls (only creators with new stories) | On demand |
| Message download | On WS event only | Real-time |

Worst case with 50 followed creators: ~2 polling calls per cycle + targeted
per-creator downloads. Compare to naive approach: 50+ calls per cycle.
