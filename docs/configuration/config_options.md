# Configuration Reference

Fansly Downloader NG reads configuration from a single **`config.yaml`** at
the working directory root. The loader uses [`ruamel.yaml`](https://yaml.readthedocs.io/),
which preserves comments, key order, and anchors across rewrites — your
hand-written YAML structure survives load → modify → dump cycles intact.

CLI flags override config values when both are present (see
[CLI ↔ config mapping](#cli--config-mapping) below).

> **Auto-migration on first load**: a legacy `config.ini` is silently
> migrated to `config.yaml` on first run; the original `.ini` is preserved
> as a backup. Retired keys (`db_sync_*`, `metadata_handling`,
> `separate_metadata`) are silently dropped from the YAML on load.

> **`extra="forbid"` everywhere**: every section enforces strict-key
> validation. A typo in a key name raises a clear `ValidationError` at
> startup rather than being silently ignored. If the loader rejects your
> file, the error message names the unknown key and the section it appeared
> in.

## Quick example

A minimal `config.yaml`:

```yaml
targeted_creator:
  usernames:
    - alice
    - bob

my_account:
  authorization_token: your_session_token_here
  user_agent: "Mozilla/5.0 …"

options:
  download_directory: /mnt/media/Fansly
  download_mode: NORMAL

postgres:
  pg_host: localhost
  pg_port: 5432
  pg_database: fansly_metadata
  pg_user: fansly_user
  pg_password: your_db_password
```

---

## `targeted_creator`

Which creator(s) to download from.

```yaml
targeted_creator:
  usernames:
    - alice
    - bob
  use_following: false
```

| Field           | Type                | Default | Description                                                                                                                  |
| --------------- | ------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `usernames`     | `list[str] \| null` | `null`  | Creator usernames to process. A comma-separated string (legacy `config.ini` shape) is auto-coerced into a list at parse time |
| `use_following` | `bool`              | `false` | When `true`, ignore `usernames` and download from every creator the account follows. Equivalent to `-uf` on the CLI          |

### Retired fields (silently dropped on load)

| Field                           | Reason                                                                                                                                                              |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `use_following_with_pagination` | CLI-only macro (`-ufp`) — toggles `use_following` AND `options.use_pagination_duplication` together at runtime. Never a real YAML setting; not consulted from there |

---

## `my_account`

Authentication credentials.

```yaml
my_account:
  authorization_token: your_session_token_here
  user_agent: "Mozilla/5.0 …"
  check_key: qybZy9-fyszis-bybxyf
  username: null
  password: null
```

| Field                 | Type                | Default                  | Description                                                                                                                                                                                       |
| --------------------- | ------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `authorization_token` | `SecretStr`         | `"ReplaceMe"`            | Fansly session token. Stored as `SecretStr` so it never accidentally prints in logs or tracebacks. Read via `.get_secret_value()` in code                                                         |
| `user_agent`          | `str`               | `"ReplaceMe"`            | Browser user-agent string sent on every HTTP request. Must match the user-agent of the browser the `authorization_token` was extracted from, or Fansly's session-binding will reject the requests |
| `check_key`           | `str`               | `"qybZy9-fyszis-bybxyf"` | Fansly's per-deployment request-signing key. Auto-extracted from `main.js` on session setup; the default here is a known-good seed for first-run config generation                                |
| `username`            | `str \| None`       | `null`                   | Account username (optional; used for the password-login flow as an alternative to `authorization_token` extraction)                                                                               |
| `password`            | `SecretStr \| None` | `null`                   | Account password (optional; pairs with `username` for the password-login flow). Stored as `SecretStr`                                                                                             |

---

## `options`

Download behaviour, output formatting, rate limiting.

```yaml
options:
  download_directory: Local_directory
  download_mode: NORMAL
  show_downloads: true
  show_skipped_downloads: true
  download_media_previews: true
  open_folder_when_finished: true
  separate_messages: true
  separate_previews: false
  separate_timeline: true
  use_duplicate_threshold: false
  use_pagination_duplication: false
  use_folder_suffix: true
  interactive: true
  prompt_on_exit: true
  timeline_retries: 1
  timeline_delay_seconds: 60
  api_max_retries: 10
  rate_limiting_enabled: true
  rate_limiting_adaptive: true
  rate_limiting_requests_per_minute: 60
  rate_limiting_burst_size: 10
  rate_limiting_retry_after_seconds: 30
  rate_limiting_backoff_factor: 1.5
  rate_limiting_max_backoff_seconds: 300
  respect_timeline_stats: true
  temp_folder: null
```

### `options` — output & UI

| Field                       | Type          | Default             | Description                                                                                                                                                                        |
| --------------------------- | ------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `download_directory`        | `str`         | `"Local_directory"` | Where downloaded files land. Per-creator subdirectories are created underneath                                                                                                     |
| `download_mode`             | enum          | `NORMAL`            | One of: `NORMAL`, `TIMELINE`, `MESSAGES`, `COLLECTION`, `WALL`, `SINGLE`, `STORIES`, `STASH_ONLY`. Case-insensitive at parse time. See [download modes reference](#download-modes) |
| `show_downloads`            | `bool`        | `true`              | Print one line per file as it downloads                                                                                                                                            |
| `show_skipped_downloads`    | `bool`        | `true`              | Print one line per file skipped due to deduplication                                                                                                                               |
| `download_media_previews`   | `bool`        | `true`              | Download free preview media on PPV-locked items                                                                                                                                    |
| `open_folder_when_finished` | `bool`        | `true`              | Open the download directory in the OS file manager when the run finishes (no-op in non-interactive / daemon mode)                                                                  |
| `separate_messages`         | `bool`        | `true`              | Place message attachments in `messages/` subdirectory rather than mixed with timeline content                                                                                      |
| `separate_previews`         | `bool`        | `false`             | Place free-preview media in `previews/` subdirectory rather than alongside the locked content                                                                                      |
| `separate_timeline`         | `bool`        | `true`              | Place timeline content in `timeline/` subdirectory                                                                                                                                 |
| `use_folder_suffix`         | `bool`        | `true`              | Append a `_(creator_id)` suffix to the per-creator folder name to disambiguate creators who change usernames                                                                       |
| `interactive`               | `bool`        | `true`              | Show interactive menus and prompts. Set `false` for cron / automation                                                                                                              |
| `prompt_on_exit`            | `bool`        | `true`              | Wait for an Enter keypress before exiting. Set `false` for non-interactive runs                                                                                                    |
| `temp_folder`               | `str \| None` | `null`              | Override the temp directory used during in-flight downloads. `null` uses the OS default                                                                                            |

### `options` — debugging

There is no YAML toggle for runtime verbosity. Pass `-v` (DEBUG floor on
every handler) or `-vv` (TRACE floor on every handler) on the command
line, or edit `logging.global.default_level` for persistent per-handler
defaults. See [`logging`](#logging) below for the per-handler levels and
the [CLI mapping table](#cli--config-mapping) for the verbosity flags.

### `options` — deduplication

| Field                        | Type   | Default | Description                                                                                                 |
| ---------------------------- | ------ | ------- | ----------------------------------------------------------------------------------------------------------- |
| `use_duplicate_threshold`    | `bool` | `false` | Stop processing a creator's timeline after a configurable threshold of consecutive already-downloaded items |
| `use_pagination_duplication` | `bool` | `false` | Same idea but at pagination granularity — stop paginating after a fully-duplicate page                      |

### `options` — retries & rate limiting

| Field                               | Type    | Default | Description                                                                                                                                                                |
| ----------------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `timeline_retries`                  | `int`   | `1`     | Number of retries on a failed timeline page fetch                                                                                                                          |
| `timeline_delay_seconds`            | `int`   | `60`    | Cooldown between retries on the same creator's timeline                                                                                                                    |
| `api_max_retries`                   | `int`   | `10`    | Total retry attempts for any API call before giving up                                                                                                                     |
| `rate_limiting_enabled`             | `bool`  | `true`  | Master switch for the outbound rate limiter                                                                                                                                |
| `rate_limiting_adaptive`            | `bool`  | `true`  | When `true`, the limiter widens its window after sustained 200s and narrows after 429s. When `false`, the configured static rate is enforced regardless of server feedback |
| `rate_limiting_requests_per_minute` | `int`   | `60`    | Target sustained outbound rate                                                                                                                                             |
| `rate_limiting_burst_size`          | `int`   | `10`    | Token-bucket capacity — max requests in a burst before refill matters                                                                                                      |
| `rate_limiting_retry_after_seconds` | `int`   | `30`    | Default wait when the server's `Retry-After` header is absent on a 429                                                                                                     |
| `rate_limiting_backoff_factor`      | `float` | `1.5`   | Multiplicative backoff applied on each rate-limit (429) response                                                                                                           |
| `rate_limiting_max_backoff_seconds` | `int`   | `300`   | Cap on the backoff sleep duration                                                                                                                                          |
| `respect_timeline_stats`            | `bool`  | `true`  | When `true`, the timeline/wall downloaders short-circuit a full scan when `TimelineStats` counts and wall structure match the local DB. Set `false` to force a full scan   |

### Retired fields (silently dropped on load)

The following keys were valid in earlier versions and are now silently
stripped from the YAML on load. Keeping them in your file is harmless:

- `separate_metadata` — SQLite-era flag; was always a no-op under Postgres
- `metadata_handling` — runtime never branched on `SIMPLE` vs `ADVANCED`
- `db_sync_commits`, `db_sync_seconds`, `db_sync_min_size` — SQLite-era
  write-sync tuning knobs; not consulted under asyncpg
- `debug`, `trace` — replaced by the `-v` / `-vv` CLI flags (per-run
  runtime override). For persistent verbosity, set
  `logging.global.default_level` instead

---

## `postgres`

PostgreSQL connection settings for asyncpg.

```yaml
postgres:
  pg_host: localhost
  pg_port: 5432
  pg_database: fansly_metadata
  pg_user: fansly_user
  pg_password: your_db_password
  pg_sslmode: prefer
  pg_sslcert: null
  pg_sslkey: null
  pg_sslrootcert: null
  pg_pool_size: 5
  pg_max_overflow: 10
  pg_pool_timeout: 30
```

| Field             | Type                | Default             | Description                                                                                                                                                                                          |
| ----------------- | ------------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pg_host`         | `str`               | `"localhost"`       | Postgres server hostname                                                                                                                                                                             |
| `pg_port`         | `int`               | `5432`              | Postgres server port                                                                                                                                                                                 |
| `pg_database`     | `str`               | `"fansly_metadata"` | Database name                                                                                                                                                                                        |
| `pg_user`         | `str`               | `"fansly_user"`     | Postgres role                                                                                                                                                                                        |
| `pg_password`     | `SecretStr \| None` | `null`              | Database password. Stored as `SecretStr` so it never appears in logs                                                                                                                                 |
| `pg_sslmode`      | `str`               | `"prefer"`          | libpq-style SSL mode. **Currently NOT wired to `asyncpg.create_pool`** — the value is parsed and stored but not passed to the pool. A future task should pass `ssl=` when any `pg_ssl*` value is set |
| `pg_sslcert`      | `str \| None`       | `null`              | SSL client certificate path. Same NOT-wired caveat as `pg_sslmode`                                                                                                                                   |
| `pg_sslkey`       | `str \| None`       | `null`              | SSL client key path. Same NOT-wired caveat                                                                                                                                                           |
| `pg_sslrootcert`  | `str \| None`       | `null`              | SSL root cert path. Same NOT-wired caveat                                                                                                                                                            |
| `pg_pool_size`    | `int`               | `5`                 | asyncpg pool `min_size`/`max_size`                                                                                                                                                                   |
| `pg_max_overflow` | `int`               | `10`                | Legacy SQLAlchemy pool setting kept for round-trip parity with `config.ini`. **Not consulted by asyncpg** — the asyncpg pool only respects `min_size`/`max_size`                                     |
| `pg_pool_timeout` | `int`               | `30`                | Same legacy / not-consulted caveat as `pg_max_overflow`                                                                                                                                              |

---

## `cache`

Runtime device-id cache for Fansly API authentication. Managed by the
API layer at runtime; **users should not edit these manually** (changes
are overwritten on the next session-setup pass).

```yaml
cache:
  device_id: null
  device_id_timestamp: null
```

| Field                 | Type          | Default | Description                                                               |
| --------------------- | ------------- | ------- | ------------------------------------------------------------------------- |
| `device_id`           | `str \| None` | `null`  | Cached Fansly device identifier; populated automatically on first connect |
| `device_id_timestamp` | `int \| None` | `null`  | Unix timestamp when the cached `device_id` was issued                     |

---

## `logging`

Eight named loggers plus a `global` defaults block. Two console handlers
and six rotating-file handlers. Each entry has its own `enabled` /
`level` / `format`; file entries additionally have orthogonal size and
time rotation axes (both can be active simultaneously — size cap _and_
hourly rotation, whichever trips first). Per-entry `null` fields fall
through to the corresponding `global.default_*`.

```yaml
logging:
  global:
    directory: ~/.fansly-downloader-ng/logs
    trace: false
    default_level: INFO
    default_max_size: 104857600 # 100 MiB
    default_rotation_when: h # hourly
    default_rotation_interval: 1
    default_utc: true
    default_backup_count: 5
    default_compression: gz
    default_keep_uncompressed: 2

  # Console handlers
  rich_handler: { enabled: true, level: INFO }
  stash_console: { enabled: true, level: INFO }

  # File handlers — each can override any default
  main_log: { enabled: true, level: INFO }
  json: { enabled: true, level: INFO, backup_count: 10 }
  stash_file: { enabled: true, level: INFO, backup_count: 10 }
  db: { enabled: true, level: INFO, backup_count: 20 }
  websocket: { enabled: true, level: INFO, backup_count: 10 }
  trace: { enabled: false, level: TRACE }
```

### `logging.global`

| YAML key                    | Default               | Description                                                                                     |
| --------------------------- | --------------------- | ----------------------------------------------------------------------------------------------- |
| `directory`                 | `null`                | Log root directory. `null` → application default (`~/.fansly-downloader-ng/logs`)               |
| `trace`                     | `false`               | Persistent trace toggle — linked bi-directionally with `trace.enabled`; setting either flips both. Opens the `trace.log` sink at TRACE; peer handlers stay at their configured level. (For a uniform TRACE floor across every handler, use the runtime `-vv` flag instead.) |
| `default_level`             | `"INFO"`              | Fallback level for any handler whose `level` is `null`                                          |
| `default_format`            | `null`                | Fallback log format (loguru format string) for any handler whose `format` is `null`             |
| `default_max_size`          | `104857600` (100 MiB) | Size axis cap. `null` disables size-based rotation                                              |
| `default_rotation_when`     | `"h"`                 | Time axis interval unit: `s`, `m`, `h`, `d`, or `midnight`. `null` disables time-based rotation |
| `default_rotation_interval` | `1`                   | Count of `default_rotation_when` units between rotations                                        |
| `default_utc`               | `true`                | Whether the time axis uses UTC                                                                  |
| `default_backup_count`      | `5`                   | Retention: number of rotated files kept. `null` retains forever                                 |
| `default_compression`       | `"gz"`                | Compression applied to rotated files: `gz`, `bz2`, `xz`, or `null` (uncompressed)               |
| `default_keep_uncompressed` | `2`                   | Most-recent N rotated files kept uncompressed for live `tail -F`                                |

### Per-handler entries

**Console handlers** (`rich_handler`, `stash_console`):

| YAML key  | Default                   | Description                                                         |
| --------- | ------------------------- | ------------------------------------------------------------------- |
| `enabled` | `true`                    | Disable to drop the handler entirely                                |
| `level`   | `null` → `default_level`  | Per-handler level floor. `TRACE` is rejected on console (file-only) |
| `format`  | `null` → `default_format` | Per-handler format override                                         |

**File handlers** (`main_log`, `json`, `stash_file`, `db`, `trace`, `websocket`):

| YAML key            | Default                              | Description                                                                                                                                                                          |
| ------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `enabled`           | `true` (`false` for `trace`)         | Disable to drop the handler                                                                                                                                                          |
| `filename`          | per-handler default                  | Output filename (joined with `global.directory`). Defaults: `fansly_downloader_ng.log`, `fansly_downloader_ng_json.log`, `stash.log`, `sqlalchemy.log`, `trace.log`, `websocket.log` |
| `level`             | `null` → `default_level`             | Per-handler level floor                                                                                                                                                              |
| `format`            | `null` → `default_format`            | Per-handler format override                                                                                                                                                          |
| `max_size`          | `null` → `default_max_size`          | Size cap in bytes. `null` disables the size axis                                                                                                                                     |
| `rotation_when`     | `null` → `default_rotation_when`     | Time interval unit. `null` disables the time axis                                                                                                                                    |
| `rotation_interval` | `null` → `default_rotation_interval` | Count of time units                                                                                                                                                                  |
| `utc`               | `null` → `default_utc`               | Whether the time axis uses UTC                                                                                                                                                       |
| `backup_count`      | `null` → `default_backup_count`      | Rotated-file retention count                                                                                                                                                         |
| `compression`       | `null` → `default_compression`       | Per-handler compression override                                                                                                                                                     |
| `keep_uncompressed` | `null` → `default_keep_uncompressed` | Most-recent N rotated files left uncompressed for live tail                                                                                                                          |

### Retired fields

Pre-v0.14 `logging:` was a flat map of `<logger>: <level>` strings. Old
files keep loading via a `model_validator(mode="before")` that lifts the
flat entries into the nested shape:

| Legacy key      | New location                              |
| --------------- | ----------------------------------------- |
| `sqlalchemy`    | `db.level`                                |
| `stash_console` | `stash_console.level`                     |
| `stash_file`    | `stash_file.level`                        |
| `textio`        | `main_log.level` AND `rich_handler.level` |
| `websocket`     | `websocket.level`                         |
| `json` (string) | `json.level`                              |
| `json_level`    | `json.level`                              |

These keys are silently dropped (no migration):

- `logging.global.debug` — replaced by `-v` CLI flag (runtime DEBUG floor
  on every handler) or `logging.global.default_level: DEBUG` (per-handler
  default for handlers whose `level` is `null`)
- `logging.global.verbose` — older alias for the same toggle, also
  retired

---

## `stash_context`

[Stash media server](https://github.com/stashapp/stash) integration
connection. **Optional** — omit the section entirely to disable Stash
integration. When absent, the downloader skips all Stash sync logic
without warning.

```yaml
stash_context:
  scheme: http
  host: localhost
  port: 9999
  apikey: ""
  mapped_path: null
  override_dldir_w_mapped: false
  require_stash_only_mode: false
```

| Field                     | Type          | Default       | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------- | ------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scheme`                  | `str`         | `"http"`      | URL scheme for the Stash server (`http` or `https`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `host`                    | `str`         | `"localhost"` | Stash server hostname                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `port`                    | `int`         | `9999`        | Stash server port                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `apikey`                  | `str`         | `""`          | Stash API key. Empty string disables authentication. Required if your Stash server has API auth enabled                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `mapped_path`             | `str \| None` | `null`        | **Docker / NFS path mapping.** Set this when Stash runs in a container that mounts your download directory under a different path prefix than the scraper sees. For example: if the scraper writes to `/home/user/downloads/` but the Stash container mounts the same share as `/data/fansly/`, set `mapped_path: /data/fansly`. The scraper will substitute the `options.download_directory` prefix with this value in every path it sends to Stash (scan jobs, path filters, regex queries). Leave `null` when both environments share identical paths                                                                                                                    |
| `override_dldir_w_mapped` | `bool`        | `false`       | **Override the download-directory tree with `mapped_path`.** Set `true` when the scraper's per-creator subfolder structure isn't preserved in Stash — for example, files copied to the Stash host and reorganised into `Videos/<studio>/` and `Photos/<studio>/` rather than living under `<creator>_fansly/`. With this flag on, the path the scraper sends to Stash is `mapped_path` itself (no creator-subfolder appended); scan jobs and path filters scope to the whole fansly area, and matching falls back to media-ID code lookups in filenames. Requires `mapped_path` to be set — config load fails otherwise. Leave `false` for the prefix-substitution semantic |
| `require_stash_only_mode` | `bool`        | `false`       | **Engage Stash integration only when `--stash-only` is the download mode.** Set `true` when your Stash server runs on a separate host that the scraper can't reach during downloads — for example, you scrape locally on a workstation, manually copy files to the Stash host, then run `--stash-only` to attribute metadata. With this flag on, regular download runs (`NORMAL`, `TIMELINE`, `MESSAGES`, `WALL`, `SINGLE`, `STORIES`, `COLLECTION`) skip every Stash code path even when `stash_context` is fully populated; `--stash-only` runs engage Stash as usual. Leave `false` to engage Stash after every download mode                                            |

---

## `monitoring`

Post-batch monitoring daemon configuration. The daemon (entered via
`--daemon` CLI flag or `daemon_mode: true` here) keeps running after the
initial batch download completes — it consumes WebSocket events plus
falls back to timeline/story polling for continuous archiving. See
[monitoring-cadence](../reference/monitoring-cadence.md) for the
intervals and the `main.js` evidence behind them.

```yaml
monitoring:
  daemon_mode: false
  active_duration_minutes: 60
  idle_duration_minutes: 120
  hidden_duration_minutes: 300
  timeline_poll_active_seconds: 180
  timeline_poll_idle_seconds: 600
  story_poll_active_seconds: 30
  story_poll_idle_seconds: 300
  session_baseline: null
  unrecoverable_error_timeout_seconds: 3600
  dashboard_enabled: true
  heartbeat_interval_minutes: 15
  livestream_recording_enabled: false
  livestream_poll_interval_seconds: 30
  livestream_manifest_poll_interval_seconds: 3
```

### `monitoring` — top-level

| Field                                 | Type   | Default | CLI equivalent    | Description                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------- | ------ | ------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `daemon_mode`                         | `bool` | `false` | `--daemon` / `-d` | Enter the post-batch monitoring daemon after the normal batch download completes. Mirrors the CLI flag — CLI takes precedence when both are set                                                                                                                                                                                    |
| `unrecoverable_error_timeout_seconds` | `int`  | `3600`  | —                 | Fatal-error escalation window. If the daemon has had **no** successful operation (poll, WS ping-pong, or dispatch) for this many seconds, exit with `DAEMON_UNRECOVERABLE`. Rate-limiter pauses, transient 5xx, and network blips do **not** escalate as long as some other operation succeeds within the window. Default = 1 hour |
| `dashboard_enabled`                   | `bool` | `true`  | —                 | Show the Rich-based live dashboard (simulator state + per-loop countdown bars) while the daemon runs. Set `false` when piping output through tools that mangle ANSI escape sequences                                                                                                                                               |
| `heartbeat_interval_minutes`          | `int`  | `15`    | —                 | How often the simulator-tick loop emits its alive log line. Lower values produce more chatter; higher values reduce log volume                                                                                                                                                                                                     |

### `monitoring` — livestream recording

Recording is **opt-in** — set `livestream_recording_enabled: true` to capture
broadcasts. The watcher polls Fansly's online-streams endpoint on the interval
below; when it sees a tracked creator go live it spawns a per-broadcast
recorder that polls the IVS HLS manifest for new segments.

| Field                                       | Type          | Default | Description                                                                                                                                             |
| ------------------------------------------- | ------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `livestream_recording_enabled`              | `bool`        | `false` | Master switch. `false` (default) suppresses the watcher and recorder entirely                                                                           |
| `livestream_poll_interval_seconds`          | `int`         | `30`    | Seconds between `/streaming/followingstreams/online` polls. Lower values catch broadcasts faster at the cost of API traffic                             |
| `livestream_manifest_poll_interval_seconds` | `int (1..15)` | `3`     | Seconds between HLS manifest refreshes inside an active recording. The ~28s IVS sliding-window buffer means values >5 risk dropping segments under load |

### `monitoring` — session baseline

| Field              | Type               | Default | CLI equivalent                   | Description                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------ | ------------------ | ------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session_baseline` | `datetime \| None` | `null`  | `--monitor-since`, `--full-pass` | Optional per-run baseline for `should_process_creator()`. When set, the filter compares against this datetime instead of each creator's stored `MonitorState.lastCheckedAt`. Setting a very old value (e.g. `2000-01-01`) effectively forces a full pass over every creator. When `null`, each creator's own `lastCheckedAt` is used. Naive datetimes are coerced to UTC at parse time |

### `monitoring` — three-tier cadence

The simulator runs a three-tier state machine (`active` → `idle` →
`hidden` → `active`); each state has its own duration window and per-
resource poll intervals. Defaults match the values in Fansly's `main.js`
(see [monitoring-cadence](../reference/monitoring-cadence.md)) — diverging
from those values measurably risks creating an anti-detection fingerprint,
so tune within narrow operational windows rather than aggressively.

| Field                          | Type  | Default | Semantics                                                              |
| ------------------------------ | ----- | ------- | ---------------------------------------------------------------------- |
| `active_duration_minutes`      | `int` | `60`    | Minutes in `active` state before transitioning to `idle`               |
| `idle_duration_minutes`        | `int` | `120`   | Minutes in `idle` before transitioning to `hidden`                     |
| `hidden_duration_minutes`      | `int` | `300`   | Minutes in `hidden` before terminating the run                         |
| `timeline_poll_active_seconds` | `int` | `180`   | Home-timeline poll interval while `active` (3 min — matches `main.js`) |
| `timeline_poll_idle_seconds`   | `int` | `600`   | Home-timeline poll interval while `idle` (10 min — matches `main.js`)  |
| `story_poll_active_seconds`    | `int` | `30`    | Story-state poll interval while `active` (matches `main.js`)           |
| `story_poll_idle_seconds`      | `int` | `300`   | Story-state poll interval while `idle` (5 min — matches `main.js`)     |

Jitter (0-10 s for timeline polls, 0-2 s for story polls) is added on top
of each base interval so the daemon's traffic pattern doesn't look
machine-precise.

### Retired fields (silently dropped on load)

| Field                  | Reason                                                                                                                                                                                                                              |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`              | Never had a runtime consumer; `daemon_mode` is the actual on/off switch                                                                                                                                                             |
| `websocket_subprocess` | Subprocess WebSocket is now the only path — `FanslyWebSocket` is a parent-side shim over a child `multiprocessing.Process` that owns the heartbeat. No toggle (the asymmetric-GIL problem the toggle solved is now fixed by design) |

---

## `logic`

Regex patterns for extracting values from Fansly's served JavaScript.
These rarely need user adjustment — the defaults track Fansly's current
`main.js` shape and are auto-applied on every fresh extraction.

```yaml
logic:
  check_key_pattern: 'this\.checkKey_\s*=\s*["'']([^"'']+)["'']'
  main_js_pattern: '\ssrc\s*=\s*"(main\..*?\.js)"'
```

| Field               | Type  | Default                                        | Description                                                                                                                                                                            |
| ------------------- | ----- | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check_key_pattern` | `str` | `r'this\.checkKey_\s*=\s*["'']([^"'']+)["'']'` | Regex matching Fansly's per-deployment request-signing key inside `main.js`. The capture group must yield the raw key string                                                           |
| `main_js_pattern`   | `str` | `r'\ssrc\s*=\s*"(main\..*?\.js)"'`             | Regex matching the `<script src="...">` tag in `index.html` that points at the current `main.js` URL. The capture group must yield the filename (Fansly versions it: `main.<hash>.js`) |

---

## Download modes

`options.download_mode` is one of:

| Mode         | Behaviour                                                                                    |
| ------------ | -------------------------------------------------------------------------------------------- |
| `NORMAL`     | Default — downloads timeline, messages, collection, wall, and stories per per-creator config |
| `TIMELINE`   | Only public-timeline posts                                                                   |
| `MESSAGES`   | Only direct-message attachments                                                              |
| `COLLECTION` | Only the user's collection (saved/liked content)                                             |
| `WALL`       | Only wall posts                                                                              |
| `SINGLE`     | Single-post download (used with `-p <post_id>`)                                              |
| `STORIES`    | Only ephemeral stories                                                                       |
| `STASH_ONLY` | Skip downloading; only push existing local metadata to Stash                                 |

Case-insensitive at parse time — `normal`, `Normal`, `NORMAL` all resolve
to `DownloadMode.NORMAL`.

---

## CLI ↔ config mapping

CLI flags override config values when both are supplied. Inverted flags
(`--no-…`, `--hide-…`) flip the corresponding bool to `false` regardless
of the YAML setting.

**Targeted creator**

| Config field                     | CLI flag                           |
| -------------------------------- | ---------------------------------- |
| `targeted_creator.usernames`     | `-u <name>[,<name>...]` / `--user` |
| `targeted_creator.use_following` | `-uf` / `--use-following`          |

**Authentication**

| Config field                     | CLI flag               |
| -------------------------------- | ---------------------- |
| `my_account.authorization_token` | `-t` / `--token`       |
| `my_account.user_agent`          | `-ua` / `--user-agent` |
| `my_account.check_key`           | `-ck` / `--check-key`  |

**Download mode** — pick at most one; absent → uses `options.download_mode`

| Config field                         | CLI flag       |
| ------------------------------------ | -------------- |
| `options.download_mode = NORMAL`     | `--normal`     |
| `options.download_mode = TIMELINE`   | `--timeline`   |
| `options.download_mode = MESSAGES`   | `--messages`   |
| `options.download_mode = COLLECTION` | `--collection` |
| `options.download_mode = SINGLE`     | `--single`     |
| `options.download_mode = STASH_ONLY` | `--stash-only` |

**Output / paths**

| Config field                 | CLI flag               |
| ---------------------------- | ---------------------- |
| `options.download_directory` | `-dir` / `--directory` |
| `options.temp_folder`        | `--temp-folder`        |

**Boolean toggles** (the negated flags set the corresponding YAML to `false`)

| Config field                         | CLI flag                                           |
| ------------------------------------ | -------------------------------------------------- |
| `options.interactive`                | `-ni` / `--non-interactive` (sets `false`)         |
| `options.prompt_on_exit`             | `-npox` / `--no-prompt-on-exit` (sets `false`)     |
| `options.use_folder_suffix`          | `-nfs` / `--no-folder-suffix` (sets `false`)       |
| `options.download_media_previews`    | `-np` / `--no-previews` (sets `false`)             |
| `options.show_downloads`             | `-hd` / `--hide-downloads` (sets `false`)          |
| `options.show_skipped_downloads`     | `-hsd` / `--hide-skipped-downloads` (sets `false`) |
| `options.open_folder_when_finished`  | `-nof` / `--no-open-folder` (sets `false`)         |
| `options.separate_messages`          | `-nsm` / `--no-separate-messages` (sets `false`)   |
| `options.separate_timeline`          | `-nst` / `--no-separate-timeline` (sets `false`)   |
| `options.separate_previews`          | `-sp` / `--separate-previews`                      |
| `options.use_duplicate_threshold`    | `-udt` / `--use-duplicate-threshold`               |
| `options.use_pagination_duplication` | `-upd` / `--use-pagination-duplication`            |
| (runtime debug — no YAML field)      | `-v` / `--verbose` (override every handler→DEBUG)  |
| (runtime trace — no YAML field)      | `-vv` (override every handler→TRACE)               |

**Retries / rate limiting**

| Config field                     | CLI flag                           |
| -------------------------------- | ---------------------------------- |
| `options.timeline_retries`       | `-tr` / `--timeline-retries`       |
| `options.timeline_delay_seconds` | `-td` / `--timeline-delay-seconds` |
| `options.api_max_retries`        | `-ar` / `--api-max-retries`        |

**Postgres**

| Config field           | CLI flag        |
| ---------------------- | --------------- |
| `postgres.pg_host`     | `--pg-host`     |
| `postgres.pg_port`     | `--pg-port`     |
| `postgres.pg_database` | `--pg-database` |
| `postgres.pg_user`     | `--pg-user`     |
| `postgres.pg_password` | `--pg-password` |

**Monitoring daemon**

| Config field                  | CLI flag                                         |
| ----------------------------- | ------------------------------------------------ |
| `monitoring.daemon_mode`      | `-d` / `--daemon` / `--monitor`                  |
| `monitoring.session_baseline` | `--monitor-since <iso-timestamp>`, `--full-pass` |

**Other / CLI-only** (no config equivalent)

| CLI flag                                   | Purpose                                                                                                               |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `-r` / `--reverse-order`                   | Process creators in reverse alphabetical order (Z→A instead of A→Z; applies to both the following list and `-u` targets) |
| `-ufp` / `--use-following-with-pagination` | CLI-only macro: enables `targeted_creator.use_following` AND `options.use_pagination_duplication` together at runtime |
| `--generate-config`                        | Scaffold a fresh `config.yaml` at the working directory and exit                                                      |
| `--show-config`                            | Print the loaded effective configuration and exit                                                                     |

---

## First-run / migration behaviour

On startup, the loader performs a one-shot migration pass against
`config.yaml`:

- **Legacy `config.ini` migration** — if a `config.yaml` doesn't exist
  but a `config.ini` does, the loader converts the `.ini` to YAML and
  preserves the original `.ini` as a backup.
- **Retired keys are silently stripped** — the `_DROPPED_FIELDS` set in
  `OptionsSection` lists keys that the project no longer reads. They're
  removed from the parsed dict before validation so `extra="forbid"`
  doesn't reject upgrade paths.
- **Comments survive** — your hand-edited comments, anchors, and key
  order are preserved across the rewrite via `ruamel.yaml` round-tripping.
- **Typos raise an error** — unknown keys that don't match a retirement
  raise `ValidationError` at startup with the unknown key name and its
  section. This catches misspellings before they silently affect behaviour.
