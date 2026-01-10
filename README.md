# `Fansly Downloader NG`: The Ultimate Content Downloading Tool

[![Downloads](https://img.shields.io/github/downloads/prof79/fansly-downloader-ng/total?color=0078d7&label=%F0%9F%94%BD%20Downloads&style=flat-square)](https://github.com/prof79/fansly-downloader-ng/releases/latest) [![Latest Release](https://img.shields.io/github/v/release/prof79/fansly-downloader-ng?color=%23b02d4a&display_name=tag&label=%F0%9F%9A%80%20Latest%20Compiled%20Release&style=flat-square)](https://github.com/prof79/fansly-downloader-ng/releases/latest)

<!--
[![Commits since latest release](https://img.shields.io/github/commits-since/prof79/fansly-downloader-ng/latest?color=orange&label=%F0%9F%92%81%20Uncompiled%20Commits&style=flat-square)](https://github.com/prof79/fansly-downloader-ng/commits/main)
[![Active Bugs](https://img.shields.io/github/issues-raw/prof79/fansly-downloader-ng/bug?color=pink&label=%F0%9F%A6%84%20Active%20Bugs&style=flat-square)](https://github.com/prof79/fansly-downloader-ng/issues?q=is%3Aissue+is%3Aopen+label%3Abug)
-->

[![Compatible with](https://img.shields.io/badge/Compatible%20with-grey?style=flat-square)](#%EF%B8%8F-setup) [![Python 3.12+](https://img.shields.io/static/v1?style=flat-square&label=%F0%9F%90%8D%20Python&message=3.12%2B&color=3c8c50)](https://www.python.org/downloads/) [![Windows](https://img.shields.io/badge/%F0%9F%AA%9F-Windows-0078D6?style=flat-square)](#%EF%B8%8F-setup) [![Linux](https://img.shields.io/badge/%F0%9F%90%A7-Linux-FCC624?style=flat-square)](#%EF%B8%8F-setup) [![macOS](https://img.shields.io/badge/%E2%9A%AA-macOS-000000?style=flat-square)](#%EF%B8%8F-setup)

![Fansly Downloader NG Screenshot](resources/fansly_ng_screenshot.png)

This is a rewrite/refactoring of [Avnsx](https://github.com/Avnsx)'s original [Fansly Downloader](https://github.com/Avnsx/fansly-downloader). **Fansly Downloader NG** supports new features:

- Full command-line support for all options
- `config.ini` not required to start the program anymore - a `config.ini` with all program defaults will be generated automatically
- Support for minimal `config.ini` files - missing options will be added from program defaults automatically
- True multi-user support - put one or more creators as a list into `config.ini` (`username = creator1, creator2, creator3`) or supply via command-line
- Run it in non-interactive mode (`-ni`) without any user intervention - eg. when downloading while being away from the computer
- You may also run it in fully silent mode without the close prompt at the very end (`-ni -npox`) - eg. running **Fansly Downloader NG** from another script or from a scheduled task/cron job
- Logs all relevant messages (`Info`, `Warning`, `Error`, ...) of the last few sessions to `fansly_downloader_ng.log`. A history of 5 log files with a maximum size of 1 MiB will be preserved and can be deleted at your own discretion.
- Easier-to-extend, modern, modular and robust codebase
- It doesn't care about starring the repository

_There are still pieces missing like an appropriate wiki update._

**Fansly Downloader NG** is the go-to app for all your bulk media downloading needs. Download photos, videos, audio or any other media from Fansly. This powerful tool has got you covered! Say goodbye to the hassle of individually downloading each piece of media – now you can download them all or just some in one go.

## ✨ Features

### 📥 Download Modes

- Bulk: Timeline, Messages, Collection
- Single Posts by post ID

### ♻️ Updates

- Easily update prior download folders
- App keeps itself up-to-date with fansly

### 🖥️ Cross-Platform Compatibility

- Compatible with Windows, Linux & MacOS
- Executable app only ships for Windows

### ⚙️ Customizability

- Separate media into sub-folders?
- Want to download previews?

### 🔎 Deduplication

- Downloads only unique content
- resulting in less bandwidth usage

### 💸 Free of Charge

- Open source, community driven project

---

📖 [Configuration Settings in detail](https://github.com/prof79/fansly-downloader-ng/wiki/Explanation-of-provided-programs-&-their-functionality#explanation-of-configini)

📋 [Detailed description on each of the components of this software](https://github.com/prof79/fansly-downloader-ng/wiki/Explanation-of-provided-programs-&-their-functionality)

## ⚠️ Breaking Changes

### PostgreSQL Migration (v0.11.0+)

**IMPORTANT:** Starting with version 0.11.0, Fansly Downloader NG has migrated from SQLite to PostgreSQL for metadata storage. This is a **breaking change** that requires action before upgrading.

#### Database Evolution Timeline

- **v0.9.9 and earlier (2024-06-28)**: No metadata database - downloads tracked by filenames only
- **v0.10.x (late 2024)**: SQLite metadata database introduced for better deduplication and tracking
- **v0.11.0+ (current)**: PostgreSQL for higher performance and data reliability

#### Why the Change?

The migration to PostgreSQL provides several critical improvements:

- **Better Performance**: Significantly faster queries and bulk operations on large datasets
- **Superior Concurrency**: True multi-user/multi-process support without database locking issues
- **Network Support**: Reliable operation with databases on network paths (NAS, SMB shares)
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

3. **Migrate Existing Data** (if upgrading from v0.10.x with SQLite)

   **Note:** If you're upgrading from v0.9.x or earlier (before metadata tracking), skip this step - there's no SQLite database to migrate.

   Use the provided migration script to transfer your existing SQLite metadata to PostgreSQL:

   ```bash
   python scripts/migrate_to_postgres.py \
       --sqlite-file metadata_db.sqlite3 \
       --pg-host localhost \
       --pg-database fansly_metadata \
       --pg-user fansly_user
   ```

   The script will:

   - Create a backup of your SQLite database (with timestamp)
   - Copy all tables and data to PostgreSQL
   - Verify the migration completed successfully
   - Optionally delete the SQLite file after successful migration (use `--delete-sqlite`)

4. **Update Configuration**

   Update your `config.ini` with PostgreSQL connection details. Use the following individual parameters in the `[Options]` section:

   ```ini
   [Options]
   pg_host = localhost
   pg_port = 5432
   pg_database = fansly_metadata
   pg_user = fansly_user
   pg_password = your_secure_password
   pg_pool_size = 5
   pg_max_overflow = 10
   pg_pool_timeout = 30
   ```

#### Backward Compatibility

**There is NO backward compatibility with SQLite.** Once you upgrade to v0.11.0+, you must use PostgreSQL. If you need to continue using SQLite, remain on v0.10.x releases.

For detailed migration instructions and troubleshooting, see the [PostgreSQL Migration Guide](scripts/migrate_to_postgres.py).

### JavaScript Dependencies (Node.js/npm Required)

**NEW REQUIREMENT:** Fansly Downloader NG now requires Node.js and specific JavaScript libraries for checkKey extraction and browser compliance detection.

#### Why These Dependencies?

Fansly's authentication system requires extracting a `checkKey` value from their JavaScript bundles. To do this reliably and securely, we use:

- **acorn**: Industry-standard JavaScript parser for AST (Abstract Syntax Tree) generation
- **acorn-walk**: AST traversal library to find specific code patterns
- **JSPyBridge** (Python `javascript` package): Required for Python-JavaScript communication

#### Installation Requirements

1. **Install Node.js**

   **Recommended**: Use [nvm (Node Version Manager)](https://github.com/nvm-sh/nvm) for easy version management:

   ```bash
   # Install nvm (macOS/Linux)
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash

   # Install Node.js (version specified in .nvmrc)
   nvm install
   nvm use
   ```

   **Alternative**: Download and install directly from [nodejs.org](https://nodejs.org/) (LTS version recommended)

2. **Install JavaScript Dependencies**

   ```bash
   # In the project directory
   npm install acorn acorn-walk
   ```

3. **Install JSPyBridge** (Required - included in Poetry dependencies)

   JSPyBridge is already included when you run `poetry install`. This provides efficient Python-JavaScript communication for checkKey extraction.

   **Note:** If you installed manually with pip instead of Poetry, run:

   ```bash
   pip install javascript
   ```

#### Version Management

The project includes an `.nvmrc` file (currently set to `v25.0.0`) for consistent Node.js versions across environments. When using nvm:

```bash
# Automatically use the correct Node.js version
nvm use
```

**Important:** JSPyBridge detects and uses your local nvm installation if available, ensuring compatibility with the Node.js version specified in `.nvmrc`.

For more details, see [CheckKey Extraction Documentation](CHECKKEY_JSPYBRIDGE.md).

## 🏗️ Setup

On Windows you can just download and run the [executable version](https://github.com/prof79/fansly-downloader-ng/releases/latest) - skip the entire setup section and go directly to [Quick Start](https://github.com/prof79/fansly-downloader-ng#-quick-start).

### Python Environment

If your operating system is not compatible with executable versions of **Fansly Downloader NG** (only Windows supported for `.exe`) or you want to use the Python sources directly, please [download and extract](https://github.com/prof79/fansly-downloader-ng/archive/refs/heads/master.zip) _or_ clone the repository and ensure that [Python 3.12+](https://www.python.org/downloads/) is installed on your system.

**Note:** This project uses [Poetry](https://python-poetry.org/) for dependency management. Poetry automatically creates and manages virtual environments for you.

#### Installation Steps

1. **Install Poetry** (if not already installed):

   ```bash
   # Linux, macOS, Windows (WSL)
   curl -sSL https://install.python-poetry.org | python3 -

   # Or using pip (alternative method)
   pip install poetry
   ```

   **Note:** You may need to add Poetry to your PATH. See [Poetry installation docs](https://python-poetry.org/docs/#installation) for details.

2. **Install Node.js and npm Dependencies:**

   Fansly Downloader NG requires Node.js for checkKey extraction from Fansly's JavaScript bundles.

   **Option A: Using nvm (Recommended)**

   ```bash
   # Install nvm (macOS/Linux)
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash

   # Navigate to project directory
   cd fansly-downloader-ng

   # Install Node.js (version specified in .nvmrc)
   nvm install
   nvm use

   # Install npm dependencies
   npm install acorn acorn-walk
   ```

   **Option B: Using Node.js directly**

   ```bash
   # Download and install Node.js LTS from nodejs.org
   # Then navigate to project directory and install npm dependencies
   cd fansly-downloader-ng
   npm install acorn acorn-walk
   ```

   **For Windows users:**

   - Download Node.js installer from [nodejs.org](https://nodejs.org/)
   - After installation, open PowerShell/CMD in the project directory
   - Run: `npm install acorn acorn-walk`

3. **Install Python Dependencies:**

   ```bash
   # Option A: Install with browser authentication support (RECOMMENDED)
   poetry install --no-root --with browser-auth

   # Option B: Install without browser authentication (username/password only)
   poetry install --no-root
   ```

   #### Understanding `--with browser-auth`

   The `browser-auth` optional dependency group enables **automatic token extraction** from your browser's storage:

   **What it does:**
   - Extracts your Fansly authentication token directly from browser storage (LevelDB for Chromium browsers, SQLite for Firefox)
   - **No need to manually copy tokens or enter credentials**
   - Supports: Chrome, Edge, Opera, Opera GX, Brave (via `plyvel-ci`), and Firefox (via built-in `sqlite3`)

   **When to use it:**
   - ✅ **Recommended** - Most convenient authentication method
   - ✅ You regularly use a supported browser to access Fansly
   - ✅ You want automatic, secure token extraction without manual steps

   **When you DON'T need it:**
   - ❌ You prefer to use username/password authentication
   - ❌ You use an unsupported browser (Safari, etc.)
   - ❌ You want minimal dependencies

   **Technical Details:**
   - Adds `plyvel-ci` (LevelDB reader for Chromium-based browsers)
   - Firefox support works without browser-auth (uses Python's built-in `sqlite3`)
   - All token extraction happens **locally** on your machine - nothing is sent to external servers

   **Note:** The `javascript` package (JSPyBridge) is automatically installed by Poetry and provides efficient Python-JavaScript communication for checkKey extraction.

4. **Additional Platform Requirements:**

   **Linux:** You may need to install the Python `Tkinter` module separately:

   ```bash
   sudo apt-get install python3-tk
   ```

   **Windows/macOS:** The `Tkinter` module is typically included in the Python installer.

#### Running the Application

```bash
# Run with Poetry
poetry run python fansly_downloader_ng.py

# Or activate the Poetry shell first, then run normally
poetry shell
python fansly_downloader_ng.py
```

#### For Developers

Install development and testing dependencies:

```bash
poetry install --no-root --with dev,typing,test,browser-auth
```

**Important:** Raw Python code versions of **Fansly Downloader NG** do not receive automatic updates. If an update is available, you will be notified but need to manually download and set up the [current repository](https://github.com/prof79/fansly-downloader-ng/archive/refs/heads/master.zip) again.

## 🚀 Quick Start

Follow these steps to quickly get started with either the [Python](https://github.com/prof79/fansly-downloader-ng#python-version-requirements) or the [Executable](https://github.com/prof79/fansly-downloader-ng/releases/latest):

1. Download the latest version of **Fansly Downloader NG** by choosing one of the options below:

   - [Windows exclusive executable version](https://github.com/prof79/fansly-downloader-ng/releases/latest) - `Fansly Downloader NG.exe`
   - [Python code version](https://github.com/prof79/fansly-downloader-ng#python-version-requirements) - `fansly_downloader_ng.py`

   and extract the files from the zip folder.

2. **Choose your authentication method:**

   **Option A: Browser Token Extraction (Recommended)**

   - **Requires:** `poetry install --no-root --with browser-auth` (see [Setup](#-setup) for details)
   - **Supported Browsers:** Chrome, Firefox, Microsoft Edge, Brave, Opera, Opera GX
   - **Platforms:** Windows 10/11, macOS, Linux
   - **How it works:**
     1. Log into your Fansly account using a supported browser
     2. Visit the Fansly website at least once to establish a session
     3. Run Fansly Downloader NG - it will automatically extract your token from browser storage
   - **Benefits:**
     - ✅ Most convenient - no manual token copying
     - ✅ Secure - tokens extracted locally, never transmitted
     - ✅ Automatic - no credentials needed in config file

   **Option B: Username/Password Login**

   - **Requires:** Basic installation: `poetry install --no-root` (no browser-auth needed)
   - **How it works:** Add your Fansly credentials to `config.ini`:

     ```ini
     [MyAccount]
     username = your_fansly_account_username
     password = your_fansly_account_password
     ```

   - **Benefits:**
     - ✅ Works without browser dependencies
     - ✅ Minimal installation requirements
     - ✅ Good for servers/headless systems
   - **Note:** The application will automatically log in and obtain a token for you

3. Open and run the `Fansly Downloader NG.exe` file by clicking on it or run `poetry run python fansly_downloader_ng.py` from a terminal. This will initiate the interactive setup tutorial for the configuration file called [`config.ini`](https://github.com/prof79/fansly-downloader-ng/wiki/Explanation-of-provided-programs-&-their-functionality#explanation-of-configini).
4. After values for the targeted creators [Username](https://github.com/prof79/fansly-downloader-ng/blob/fc7c6734061f6b61ddf3ef3ae29618aedc21e052/config.ini#L2), your Fansly account [Authorization Token](https://github.com/prof79/fansly-downloader-ng/blob/fc7c6734061f6b61ddf3ef3ae29618aedc21e052/config.ini#L5) and your web browser's [User-Agent](https://github.com/prof79/fansly-downloader-ng/blob/fc7c6734061f6b61ddf3ef3ae29618aedc21e052/config.ini#L6) are filled you're good to go 🎉!
   See the [manual set-up tutorial](https://github.com/prof79/fansly-downloader-ng/wiki/Get-Started) if anything could not be configured automatically.

Once you have completed the initial configuration of **Fansly Downloader NG**, for every future use case, you will only need to adapt the creator(s) in `Targeted Creator > Username` section in the `config.ini` using a text editor of your choice. Additional settings can also be found in the `config.ini` file, which are documented in [the Wiki](https://github.com/prof79/fansly-downloader-ng/wiki/Explanation-of-provided-programs-&-their-functionality#4-configini) page.

## 🤔 FAQ

Do you have any unanswered questions or want to know more about **Fansly Downloader NG**? Head over to the [Wiki](https://github.com/prof79/fansly-downloader-ng/wiki) or check if your topic was mentioned in [Discussions](https://github.com/prof79/fansly-downloader-ng/discussions) or [Issues](https://github.com/prof79/fansly-downloader-ng/issues)

- **Q**: "Is **Fansly Downloader NG** exclusive to Windows?"
- **A**: No, **Fansly Downloader NG** can be ran on Windows, MacOS or Linux. It's just that the executable version of the downloader, is currently only being distributed for the windows 10 & 11 operating systems. You can use **Fansly Downloader NG** from the [raw Python sources](https://github.com/prof79/fansly-downloader-ng#%EF%B8%8F-set-up) on any other operating system and it'll behave the exact same as the Windows executable version.
- **Q**: "Is it possible to download Fansly files on a mobile device?"
- **A**: Unfortunately, downloading Fansly files on a mobile device is currently not supported by **Fansly Downloader NG** or any other available means.
- **Q**: "Why do some executables show detections on them in VirusTotal?"
- **A**: The **Fansly Downloader NG** executables are not [digitally signed](https://www.digicert.com/signing/code-signing-certificates) as software certificates are very expensive. Thus the executables tend to produce a lot of false positives (invalid detections). Antivirus providers can be mailed to update their detections but not all do care.
  If you're knowledgeable with the Python programming language you can decompile a [PyInstaller](https://github.com/pyinstaller/pyinstaller) executable such as **Fansly Downloader NG** using a tool like [uncompyle6](https://github.com/rocky/python-uncompyle6/) - and assure yourself that no harmful code is included. Or you could just create your own [PyInstaller](https://github.com/pyinstaller/pyinstaller) executable.
- **Q**: "Could you add X feature or do X change?"
- **A**: I'm regrettably very limited on time and thus primarily do stuff I find useful myself. You can contribute code by [opening a pull request](https://github.com/prof79/fansly-downloader-ng/pulls)
- **Q**: "Will you add any payment bypassing features to **Fansly Downloader NG**?"
- **A**: No, as the intention of this repository is not to harm Fansly or it's content creators.
- **Q**: "Is there a possibility of being banned?"
- **A**: While there are no guarantees, it's worth noting that among the 24.000+ previous users, there have been no reported incidents.

Please note that "Issue" tickets are reserved for reporting genuine or suspected bugs in the codebase of the downloader which require attention from the developer. They are not for general computer user problems.

## 🤝 Contributing to Fansly Downloader NG

Any kind of positive contribution is welcome! Please help the project improve by [opening a pull request](https://github.com/prof79/fansly-downloader-ng/pulls) with your suggested changes!

### Special Thanks

A heartfelt thank you goes out to [@liviaerxin](https://github.com/liviaerxin) for their invaluable contribution in providing the cross-platform package [plyvel](https://github.com/wbolster/plyvel). Due to [these builds](https://github.com/liviaerxin/plyvel/releases/latest) Fansly downloader NG's initial interactive cross-platform setup has become a reality.

## 🛡️ License

This project (including executables) is licensed under the GPL-3.0 License - see the [`LICENSE`](LICENSE) file for details.

## Disclaimer

"Fansly" or [fansly.com](https://fansly.com/) is operated by Select Media LLC as stated on their "Contact" page. This repository and the provided content in it isn't in any way affiliated with, sponsored by, or endorsed by Select Media LLC or "Fansly". The developer(referred to: "prof79" in the following) of this code is not responsible for the end users actions, no unlawful activities of any kind are being encouraged. Statements and processes described in this repository only represent best practice guidance aimed at fostering an effective software usage. The repository was written purely for educational purposes, in an entirely theoretical environment. Thus, any information is presented on the condition that the developer of this code shall not be held liable in no event to you or anyone else for any direct, special, incidental, indirect or consequential damages of any kind, or any damages whatsoever, including without limitation, loss of profit, loss of use, savings or revenue, or the claims of third parties, whether the developer has advised of the possibility of such loss, however caused and on any theory of liability, arising out of or in connection with the possession, use or performance of this software. The material embodied in this repository is supplied to you "as-is" and without warranty of any kind, express, implied or otherwise, including without limitation, any warranty of fitness. This code does not bypass any paywalls & no end user information is collected during usage. Finally it is important to note that this GitHub repository is the sole branch maintained and owned by the developer and any third-party websites or entities, that might refer to or be referred from it are in no way affiliated with Fansly Downloader, either directly or indirectly. This disclaimer is preliminary and is subject to revision.
