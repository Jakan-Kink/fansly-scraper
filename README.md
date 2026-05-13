# `Fansly Downloader NG`: The Ultimate Content Downloading Tool

[![Downloads](https://img.shields.io/github/downloads/Jakan-Kink/fansly-scraper/total?color=0078d7&label=%F0%9F%94%BD%20Downloads&style=flat-square)](https://github.com/Jakan-Kink/fansly-scraper/releases/latest) [![Latest Release](https://img.shields.io/github/v/release/Jakan-Kink/fansly-scraper?color=%23b02d4a&display_name=tag&label=%F0%9F%9A%80%20Latest%20Release&style=flat-square)](https://github.com/Jakan-Kink/fansly-scraper/releases/latest)

<!--
[![Commits since latest release](https://img.shields.io/github/commits-since/Jakan-Kink/fansly-scraper/latest?color=orange&label=%F0%9F%92%81%20Uncompiled%20Commits&style=flat-square)](https://github.com/Jakan-Kink/fansly-scraper/commits/main)
[![Active Bugs](https://img.shields.io/github/issues-raw/Jakan-Kink/fansly-scraper/bug?color=pink&label=%F0%9F%A6%84%20Active%20Bugs&style=flat-square)](https://github.com/Jakan-Kink/fansly-scraper/issues?q=is%3Aissue+is%3Aopen+label%3Abug)
-->

[![Compatible with](https://img.shields.io/badge/Compatible%20with-grey?style=flat-square)](#%EF%B8%8F-setup) [![Python 3.12+](https://img.shields.io/static/v1?style=flat-square&label=%F0%9F%90%8D%20Python&message=3.12%2B&color=3c8c50)](https://www.python.org/downloads/) [![Windows](https://img.shields.io/badge/%F0%9F%AA%9F-Windows-0078D6?style=flat-square)](#%EF%B8%8F-setup) [![Linux](https://img.shields.io/badge/%F0%9F%90%A7-Linux-FCC624?style=flat-square)](#%EF%B8%8F-setup) [![macOS](https://img.shields.io/badge/%E2%9A%AA-macOS-000000?style=flat-square)](#%EF%B8%8F-setup)

![Fansly Downloader NG Screenshot](resources/fansly_ng_screenshot.png)

**Fansly Downloader NG** is a content scraping and archival tool for Fansly. Download photos, videos, audio, and other media — in bulk or selectively — from timelines, messages, walls, stories, collections, and individual posts. A **continuous monitoring daemon** watches creators in near real time via Fansly's WebSocket and a calibrated polling fallback, so your local archive can stay current long after the initial bulk download completes. Optional **AWS IVS livestream recording** captures broadcasts to MP4 as they happen.

Originally forked from [Avnsx](https://github.com/Avnsx)'s [Fansly Downloader](https://github.com/Avnsx/fansly-downloader) and [prof79](https://github.com/prof79)'s [Fansly Downloader NG](https://github.com/prof79/fansly-downloader-ng) (now dormant since June 2024). Active development continues here at [Jakan-Kink/fansly-scraper](https://github.com/Jakan-Kink/fansly-scraper).

## ✨ Features

### 📥 Download Modes

- **Bulk**: Timeline, Messages, Wall, Stories, Collection
- **Single Posts** by post ID
- **Stash-only**: Re-ingest existing local library into Stash without re-downloading

### 🛰️ Monitoring Daemon

- Post-batch continuous monitoring via `--daemon` / `-d` / `--monitor`
- Live WebSocket event dispatch for new posts, stories, PPV, message edits/deletes, and subscription changes
- Activity simulator (three-tier: active → idle → hidden) matches real browser behavior to stay under the radar
- Timeline + story polling fallback when the socket is quiet
- Skips inactive creators automatically — no wasted work on accounts that haven't posted since last check
- Persistent `MonitorState` table so daemon restarts don't re-trigger every story or cold-scan every timeline
- Per-creator scope filter on WebSocket events: with `-u alice,bob` the daemon ignores events from creators outside that set, matching the polling loop's existing behavior

### 📺 Livestream Recording (Opt-In)

- Records AWS IVS low-latency HLS broadcasts (Fansly's `*.live-video.net` pipeline) to MP4 as the creator goes live
- Manual segment polling with PyAV mux — robust against the IVS sliding-window buffer (~28s) where a stalled `ffmpeg` would drop footage
- Overwrite-protection on broadcast reconnects: a mid-stream reconnect writes `<stem>_part2.mp4`, `_part3.mp4`, ... instead of clobbering the prior session's completed file
- Orphan-segment salvage: temp segment directories left behind by a prior crash are re-muxed at watcher startup
- Opt-in: set `monitoring.livestream_recording_enabled: true` in `config.yaml`. Default is `false` so existing setups don't suddenly start writing video to disk

### 🧑‍🤝‍🧑 Multi-Creator + Automation

- Download one or many creators by username (`-u alice,bob`)
- Or follow your full Fansly following list (`-uf` / `-ufp`)
- Fully non-interactive mode (`-ni -npox`) for `cron`, Task Scheduler, systemd, etc.
- Distinct exit codes for automation integrations (see [Automation Support](#automation-support))

### 🗄️ Stash Integration (Optional)

- Push Fansly metadata (creators → Performers, posts → Scenes/Galleries, tags, etc.) into a [Stash](https://github.com/stashapp/stash) media server
- Uses the [`stash-graphql-client`](https://github.com/Jakan-Kink/stash-graphql-client) PyPI package (v0.12+)
- Requires Stash server **v0.30.0+** (appSchema 75+)

### 🔎 Deduplication

- Content-hash–based dedupe (not filename-based) stored in the PostgreSQL database
- Automatic migration of legacy `_hash_` / `_hash1_` / `_hash2_` filename formats

### 🖥️ Cross-Platform

- Works on Windows, Linux, and macOS
- Python 3.12+ required
- Windows `.exe` builds are legacy artifacts of the prof79 era; active development ships Python sources only

### 💸 Free & Open Source

- GPL-3.0 licensed
- Community-driven; PRs welcome

---

📖 In-repo documentation lives under [`docs/`](docs/) — organized into `planning/` (design docs and architecture notes), `reference/` (protocol breakdowns and mapping tables), and `testing/` (testing requirements and migration guides). A MkDocs-rendered site is planned; the upstream GitHub wiki was intentionally not adopted.

📋 [Configuration Reference — `config.sample.yaml`](config.sample.yaml)

🛰️ [Monitoring Daemon Architecture](docs/planning/monitoring-daemon-architecture.md)

## ⚠️ Breaking Changes

### PostgreSQL Migration (v0.11.0+)

**IMPORTANT:** Starting with v0.11.0, Fansly Downloader NG migrated from SQLite to PostgreSQL for metadata storage. This is a **breaking change** that requires action before upgrading from any v0.10.x or earlier release.

#### Database Evolution Timeline

- **v0.9.9 and earlier (2024-06-28)**: No metadata database — downloads tracked by filenames only
- **v0.10.x (late 2024)**: SQLite metadata database introduced for better deduplication and tracking
- **v0.11.0 onward**: PostgreSQL for higher performance, concurrency, and data reliability

#### Why the Change?

The migration to PostgreSQL provides several critical improvements:

- **Better Performance**: Significantly faster queries and bulk operations on large datasets
- **Superior Concurrency**: True multi-user/multi-process support without database locking issues
- **Network Support**: Reliable operation with databases on network paths (NAS, SMB shares) — no more SQLite `db_sync_*` workaround flags
- **Advanced Features**: Better support for complex queries, transactions, and data integrity

#### Migration Requirements

##### PostgreSQL must be installed and configured before running v0.11.0+

1. **Install PostgreSQL**
   - **macOS**: `brew install postgresql@17` or download from [postgresql.org](https://www.postgresql.org/download/macosx/)
   - **Linux**: `sudo apt-get install postgresql postgresql-contrib` or equivalent for your distro
   - **Windows**: Download installer from [postgresql.org](https://www.postgresql.org/download/windows/)

2. **Create Database and User**

   ```bash
   # Start PostgreSQL service
   # macOS (Homebrew): brew services start postgresql@17
   # Linux: sudo systemctl start postgresql
   # Windows: Service starts automatically after installation

   # Create database and user
   createdb fansly_metadata
   psql -d fansly_metadata -c "CREATE USER fansly_user WITH PASSWORD 'your_secure_password';"
   psql -d fansly_metadata -c "GRANT ALL PRIVILEGES ON DATABASE fansly_metadata TO fansly_user;"
   psql -d fansly_metadata -c "GRANT ALL ON SCHEMA public TO fansly_user;"
   ```

3. **Migrate Existing Data** (only if upgrading from v0.10.x with SQLite)

   Use the provided migration script to transfer your existing SQLite metadata to PostgreSQL:

   ```bash
   python scripts/migrate_to_postgres.py \
       --sqlite-file metadata_db.sqlite3 \
       --pg-host localhost \
       --pg-database fansly_metadata \
       --pg-user fansly_user
   ```

   The script will back up your SQLite database, copy all tables and data to PostgreSQL, and verify the migration. Pass `--delete-sqlite` to remove the original SQLite file after a successful migration.

4. **Update Configuration**

   Edit your `config.yaml` (the primary config format since v0.12 — legacy `config.ini` files auto-migrate on first run):

   ```yaml
   postgres:
     pg_host: localhost
     pg_port: 5432
     pg_database: fansly_metadata
     pg_user: fansly_user
     pg_password: your_secure_password
     pg_pool_size: 5
   ```

   See [`config.sample.yaml`](config.sample.yaml) for the full schema.

#### Backward Compatibility

**There is NO backward compatibility with SQLite.** Once you upgrade to v0.11.0+, you must use PostgreSQL. If you need to continue using SQLite, remain on v0.10.x releases.

### JavaScript Dependencies (Node.js / npm Required)

Fansly Downloader NG requires Node.js for extracting a `checkKey` value from Fansly's JavaScript bundles. This uses:

- **acorn** — industry-standard JavaScript parser for AST generation
- **acorn-walk** — AST traversal library
- **JSPyBridge** (Python `javascript` package) — installed automatically by Poetry

#### Installation

1. **Install Node.js**

   **Recommended**: Use [nvm](https://github.com/nvm-sh/nvm) for easy version management:

   ```bash
   # Install nvm (macOS/Linux)
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash

   # Install the Node.js version from .nvmrc
   nvm install
   nvm use
   ```

   Alternatively, install the LTS release from [nodejs.org](https://nodejs.org/) directly.

2. **Install JavaScript Dependencies**

   ```bash
   npm install acorn acorn-walk
   ```

For more details see [CheckKey Extraction Documentation](docs/reference/CHECKKEY_JSPYBRIDGE.md).

## 🏗️ Setup

**Fansly Downloader NG** is distributed as Python source. Ensure [Python 3.12+](https://www.python.org/downloads/) is installed. This project uses [Poetry](https://python-poetry.org/) for dependency management — Poetry creates and manages its own virtual environment.

### Installation Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/Jakan-Kink/fansly-scraper.git
   cd fansly-scraper
   ```

2. **Install Poetry** (if not already installed):

   ```bash
   # Linux, macOS, Windows (WSL)
   curl -sSL https://install.python-poetry.org | python3 -

   # Or via pip
   pip install poetry
   ```

   See the [Poetry installation docs](https://python-poetry.org/docs/#installation) for platform-specific notes.

3. **Install Node.js and npm Dependencies** (see [JavaScript Dependencies](#javascript-dependencies-nodejs--npm-required) above):

   ```bash
   nvm install && nvm use
   npm install acorn acorn-walk
   ```

4. **Install Python Dependencies**

   ```bash
   # Option A: With browser authentication (RECOMMENDED)
   poetry install --no-root --with browser-auth

   # Option B: Without browser authentication (username/password only)
   poetry install --no-root
   ```

   #### Understanding `--with browser-auth`

   The `browser-auth` optional dependency group enables **automatic token extraction** from your browser's storage — no need to copy tokens or enter credentials manually.
   - **What it does**: Pulls your Fansly auth token directly from browser storage (LevelDB for Chromium browsers, SQLite for Firefox)
   - **Supported browsers**: Chrome, Microsoft Edge, Opera, Opera GX, Brave (via `plyvel-ci`), and Firefox (via built-in `sqlite3`)
   - **Privacy**: All token extraction happens locally on your machine — nothing is sent to external servers

   Skip this group if you prefer username/password auth or your browser isn't supported.

5. **Additional Platform Requirements**

   **Linux**: install the `Tkinter` module separately if missing:

   ```bash
   sudo apt-get install python3-tk
   ```

   **Windows / macOS**: `Tkinter` is typically bundled with the Python installer.

### Running the Application

```bash
# With Poetry
poetry run python fansly_downloader_ng.py

# Or activate Poetry's shell first
poetry shell
python fansly_downloader_ng.py
```

### For Developers

```bash
poetry install --no-root --with dev,typing,test,browser-auth
```

**Note:** Python source installations do **not** self-update. You will need to `git pull` manually (or re-clone) to pick up new releases. For users who prefer not to use Git, source-zip artifacts are published alongside each tagged release on the [Releases page](https://github.com/Jakan-Kink/fansly-scraper/releases/latest).

## 🚀 Quick Start

1. **Complete [Setup](#%EF%B8%8F-setup) above** — ensure Python 3.12+, Poetry, Node.js, and PostgreSQL are installed and running.

2. **Choose your authentication method**

   **Option A: Browser Token Extraction (Recommended)**

   Requires `poetry install --no-root --with browser-auth`. Supported browsers: Chrome, Firefox, Edge, Brave, Opera, Opera GX. Just log into Fansly in your browser and run the downloader — it extracts the token automatically.

   **Option B: Username / Password**

   Works without browser-auth. Add credentials to `config.yaml`:

   ```yaml
   my_account:
     username: your_fansly_account_username
     password: your_fansly_account_password
   ```

   The application logs in and obtains a token for you.

3. **Run it**

   ```bash
   poetry run python fansly_downloader_ng.py
   ```

   The first run generates a `config.yaml` in the working directory and walks you through interactive setup. If a legacy `config.ini` is present, it is migrated to `config.yaml` automatically and backed up as `config.ini.bak.<timestamp>`.

4. **Configure targeted creators** in `config.yaml`:

   ```yaml
   targeted_creator:
     usernames:
       - creator1
       - creator2
   ```

   Or pass them via CLI: `-u creator1,creator2`.

### Common CLI Invocations

```bash
# Interactive default run
poetry run python fansly_downloader_ng.py

# Fully automated (cron / Task Scheduler / systemd timer)
poetry run python fansly_downloader_ng.py -ni -npox

# Specific creators
poetry run python fansly_downloader_ng.py -u creator1,creator2,creator3

# Specific download mode (one flag per mode — pick exactly one)
poetry run python fansly_downloader_ng.py --timeline
# or: --normal | --messages | --collection | --single <post-id> | --stash-only
# (WALL and STORIES modes are config-only: set `options.download_mode: WALL`
#  or STORIES in config.yaml — no CLI flag)

# Verbose output: -v floors every handler at DEBUG, -vv at TRACE
poetry run python fansly_downloader_ng.py -u creator1 -vv

# Batch download, then watch forever via the monitoring daemon
poetry run python fansly_downloader_ng.py -u creator1 --daemon
```

## 🛰️ Monitoring Daemon

Once the initial bulk download completes, appending `--daemon` (aliases: `-d`, `--monitor`) switches the process into a long-running monitoring loop that keeps your local archive current as new content drops.

**Under the hood:**

- A single **Fansly WebSocket** connection receives live events for PPV purchase, new messages, message edits/deletes, subscription changes, and profile updates.
- Events are translated into typed `WorkItem`s (e.g., `DownloadTimelineOnly`, `DownloadStoriesOnly`, `DownloadMessagesForGroup`, `FullCreatorDownload`) and processed sequentially by the daemon runner.
- A **timeline/story poller** acts as a fallback for creators the WebSocket has no event for in a given window, using dramatically reduced request volume versus a full rescan.
- The **`ActivitySimulator`** drives poll cadence through a three-tier state machine (active → idle → hidden) calibrated against real browser-session profiling, minimizing detection surface.
- A **Rich live dashboard** surfaces per-creator state, current phase, WebSocket health, and queued work.

**Scope flags work the same as batch mode:**

- `-u alice,bob` → monitor only those creators
- `-uf` / `-ufp` → monitor the full following list (refreshed when a new subscription is confirmed)

**Stop it** with Ctrl-C (SIGINT); the daemon drains the queue cleanly.

For the full design document, see [`docs/planning/monitoring-daemon-architecture.md`](docs/planning/monitoring-daemon-architecture.md).

## Automation Support

Distinct exit codes let you wire `fansly_downloader_ng.py` into shell pipelines and orchestrators:

| Code  | Meaning             |
| ----- | ------------------- |
| `0`   | Success             |
| `1`   | Config error        |
| `2`   | API error           |
| `3`   | Download error      |
| `4`   | Some users failed   |
| `10`  | Unexpected error    |
| `255` | User abort (SIGINT) |

## 🤔 FAQ

- **Q**: "Is **Fansly Downloader NG** exclusive to Windows?"
- **A**: No — Windows, macOS, and Linux are all supported via Python sources. Windows `.exe` builds were a legacy artifact of the prof79 era and are not actively produced for the fork.

- **Q**: "Can I run this on a mobile device?"
- **A**: No. Mobile is not supported.

- **Q**: "Can you add feature X?"
- **A**: [Open an issue](https://github.com/Jakan-Kink/fansly-scraper/issues) or [Open a pull request](https://github.com/Jakan-Kink/fansly-scraper/pulls).

- **Q**: "Will you add payment-bypass features?"
- **A**: No. This tool is for archiving content you already have legitimate access to. This WILL NEVER bypass paywalls or access controls.

- **Q**: "Is there a risk of being banned?"
- **A**: There is always that possibility when using any third-party tool that interacts with a web service. However, the monitoring daemon specifically calibrates its polling cadence against real browser behavior (based on the javascript timing from Fansly's own UI code) to reduce detection surface.

- **Q**: "What happened to the original prof79 repo?"
- **A**: prof79 has not touched the project since June 28, 2024 (v0.9.9). All development through v0.10.x, v0.11.x, v0.13.x, and v0.14.x happens on the [Jakan-Kink fork](https://github.com/Jakan-Kink/fansly-scraper).

Please note that "Issue" tickets are reserved for reporting genuine or suspected bugs that require attention from a developer.

## 🤝 Contributing

Contributions welcome! [Open a pull request](https://github.com/Jakan-Kink/fansly-scraper/pulls) with your changes. Please follow the existing style and include tests where appropriate. For major features or architectural changes, consider opening an issue first to discuss the design.

### Special Thanks

- [@Avnsx](https://github.com/Avnsx) — original Fansly Downloader
- [@prof79](https://github.com/prof79) — Fansly Downloader NG rewrite (v0.5 – v0.9.9)
- [@liviaerxin](https://github.com/liviaerxin) — cross-platform [plyvel](https://github.com/wbolster/plyvel) builds that made browser auth possible
- Contributors `KasumiDev`, `FletcherD`, `XelaRellum`, `sunbart`, `pawnstar81`, `UpAndDown666`, `icewinterberry12`, `1gintonic` for historical contributions

## 🛡️ License

This project (including any executables) is licensed under the GPL-3.0 License — see [`LICENSE`](LICENSE) for details.

## Disclaimer

"Fansly" or [fansly.com](https://fansly.com/) is operated by Select Media LLC. This repository and its contents are not in any way affiliated with, sponsored by, or endorsed by Select Media LLC or "Fansly". The maintainers of this code are not responsible for end-user actions; no unlawful activities of any kind are encouraged. Statements and processes described in this repository represent best-practice guidance aimed at effective software usage. The repository was written purely for educational purposes in an entirely theoretical environment. Any information is presented on the condition that the maintainers shall not be held liable for any direct, special, incidental, indirect, or consequential damages of any kind — including loss of profit, loss of use, savings, or revenue, or the claims of third parties — arising out of or in connection with the possession, use, or performance of this software. The material is supplied "as-is" without warranty of any kind, express or implied. This code does not bypass any paywalls and no end-user information is collected during usage. This GitHub repository is the sole branch maintained by the current developer; any third-party websites or entities that might refer to or from it are in no way affiliated with Fansly Downloader, either directly or indirectly. This disclaimer is preliminary and subject to revision.
