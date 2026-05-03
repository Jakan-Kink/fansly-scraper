# Add livestream recording to fansly-downloader-ng

## Context

Fansly creators broadcast live to their subscribers. The downloader currently has no
ability to detect or record those broadcasts — when a creator goes live, the content
is gone after the stream ends unless someone manually captures it. This feature adds
automatic livestream detection, video recording, and chat capture as a sidecar.

The tool is well positioned for this:

- **HLS pipeline already exists.** `download/m3u8.py` is a 738-line three-tier
  downloader (PyAV → ffmpeg → manual segments) currently locked to VOD playlists at
  `download/m3u8.py:156` (`if playlist.is_endlist is True and playlist.playlist_type == "vod"`).
- **WebSocket runs on a dedicated thread (or subprocess, opt-in).** Per
  commit `700c5237b`, `FanslyWebSocket` runs its transport
  (connect/listen/ping) on its own thread with a private asyncio loop.
  Inbound events are marshalled back to the main loop via
  `_dispatch_event` (`api/websocket.py:1215`) using
  `run_coroutine_threadsafe` for async handlers. Lifecycle:
  `start_in_thread()` (line 1094) / `stop_thread(join_timeout=10.0)`
  (line 1182). Per commit `912b7892d`, an opt-in subprocess-isolated
  variant `FanslyWebSocketProxy` (`api/websocket_subprocess.py`)
  mirrors the same public surface for users who hit GIL contention
  with main-thread CPU bursts; enabled via
  `monitoring.websocket_subprocess: true` in `config.yaml`. Either
  way, handlers run on the main loop after marshalling — recording
  task spawns work without cross-thread/cross-process plumbing. v1
  doesn't touch the WS at all (polling-only detection), so this is
  background context, not a constraint.
- **WS handler dispatch is single-callback-per-type.**
  `_event_handlers: dict[int, Callable]` at `api/websocket.py:160` and
  `register_handler` at line 304 *replaces* the entry; the bootstrap claims
  `MSG_SERVICE_EVENT` for buffering at `daemon/bootstrap.py:122`. v1
  does **not** touch this — detection is polling-based, so the watcher
  doesn't need its own WS handler. A list-of-callables refactor would
  only be needed for a future WS-based detection enhancement.
- **`scripts/ws_monitor.py`** has a catch-all dump path
  (`scripts/ws_monitor.py:494-500`) — useful for the deferred
  WS-detection work, not needed for v1.
- **MediaLocation already models multi-CDN delivery.** `metadata/models.py:1085`
  stores `(mediaId, locationId, location)` rows where `locationId` is `1=Direct`,
  `102=DASH`, `103=HLS`. A live recording is a Media row with a MediaLocation
  pointing at the live manifest URL — no new top-level entity needed for v1.
- **The `streaming` field is silently dropped.** `FanslyObject` uses
  `extra="ignore"` (`metadata/models.py:512`) and `Account.__tracked_fields__`
  (`metadata/models.py:1917`) excludes `streaming`, so the API's
  `streaming: {accountId, channel, enabled}` payload never lands on instances.
  Adding the field is a small, localized change.

User has decided:

- **Daemon-only trigger** with the watcher launched **at script start**, not
  gated behind post-batch polling. A live broadcast that begins while the batch
  download is running must be caught.
- **Chat to JSONL sidecar** (`<recording>.chat.jsonl`) — no DB schema changes.
- **Two-phase: discover then build.** Capture real
  `streaming.channel` payloads + IVS manifest characteristics before
  committing to field shapes. (Done as of 2026-05-03 — see Phase 1.)
- **Confirmed: AWS IVS Low-Latency HLS.** A live-broadcast manifest captured
  2026-05-03 confirms the user's hunch. Host pattern is
  `<id>.j.cloudfront.hls.live-video.net` (Amazon IVS CDN). Profile is the
  **low-latency** variant: `EXT-X-TARGETDURATION:2` (2-second segments,
  not standard 6s), with IVS's custom tag namespace
  (`EXT-X-NET-LIVE-VIDEO-*`, `EXT-X-DATERANGE CLASS="live-video-net-stream-source"`,
  `EXT-X-PREFETCH`). Sliding-window confirmed: no `EXT-X-PLAYLIST-TYPE`,
  no `EXT-X-ENDLIST`, ~14 segments visible at any time → **~28-second
  buffer**. **No DRM** (`EXT-X-KEY` absent — segments unencrypted, no
  key-rotation handling needed). Auth is per-session `?dna=<token>`
  query param, identical across segments — **NOT** CloudFront signed
  cookies, so `download.m3u8.get_m3u8_cookies()` does not apply for IVS
  segment fetches; the `dna` param must be preserved verbatim. Captured
  evidence + characteristics live in
  `~/.claude/projects/-Users-shawn-Developer-fansly-downloader-ng/memory/project_livestream_ivs.md`.

## Phase 1 — Discovery (essentially complete)

The IVS manifest, API discovery endpoints, and `streaming` payload
shape are all verified by primary evidence captured 2026-05-03 (see
`project_livestream_ivs.md` memory). What's left for Phase 1 is just
codifying the captured payloads as test fixtures and documenting the
end-of-stream-signal observation:

1. Save the captured `livesuggestions` response as
   `tests/json/livestream/livesuggestions_response.json`.
2. Save the (currently-empty) `followingstreams/online` response as
   `tests/json/livestream/followingstreams_online_empty.json`. Once a
   followed creator goes live, capture the populated form too as
   `followingstreams_online_live.json`.
3. **End-of-stream observation** (the one remaining empirical
   question): when a tracked creator stops broadcasting, does the
   `streaming.channel` field on their profile flip to null? Or does
   it linger with stale data? Watch a live broadcaster, wait for them
   to end, then refetch — capture before/after as
   `streaming_field_after_stream_end.json`. This determines whether
   the watcher's end-of-stream signal is "creator dropped from
   `followingstreams/online`" (the simple path) or "fetch creator
   profile and check `streaming.channel`" (the redundant safety net).

**WebSocket-based detection is deferred** to a future enhancement —
the polling path via `followingstreams/online` is sufficient for v1
and avoids the complexity of reverse-engineering svc=45 events.

## Phase 2 — Implementation

### Files to create

- `download/livestream.py` — recording engine: master-URL → variant
  → sliding-window segment poller, MP4 mux, graceful-stop, hash on
  finalize.
- `daemon/livestream_watcher.py` — long-lived task that polls
  `/api/v1/streaming/followingstreams/online` every ~30s, diffs the
  result against `dict[creator_id, RecordingTask]`, spawns recorders
  for new lives, signals stop for ended ones.
- `daemon/livestream_chat.py` — opens a chatws connection per
  active recording (using `streaming.channel.chatRoomId`) and writes
  messages as JSONL.
- `api/fansly.py` — add two thin methods:
  `get_following_streams_online()` and `get_live_suggestions(limit, offset)`.
- `tests/fixtures/livestream/` — Phase 1 captured payloads + factories
  for `Streaming` / `StreamChannel` / `Stream` Pydantic types.
- `tests/daemon/unit/test_livestream_watcher.py`,
  `tests/download/unit/test_livestream.py` — diff-based detection,
  manifest poll loop, segment-window dedup, ffmpeg process management.

### Files to modify

- `metadata/models.py` — add three new Pydantic types matching the
  verified API shape:
  - `Streaming(FanslyRecord)`: `accountId: int`, `enabled: bool`,
    `channel: StreamChannel | None`.
  - `StreamChannel(FanslyRecord)`: `id, accountId, playbackUrl,
    chatRoomId, status, version, createdAt, updatedAt, arn,
    ingestEndpoint`, plus `stream: StreamSession | None`.
  - `StreamSession(FanslyRecord)`: `id, historyId, channelId,
    accountId, title, status, viewerCount, version, createdAt,
    updatedAt, lastFetchedAt, startedAt`. (Skip
    `permissions.permissionFlags` for v1 — informational only,
    can be added later if needed.)
  Add `streaming` to `Account.__tracked_fields__` (line 1917) and
  `__relationships__` (line 1940) as a `has_one` relationship.
- `metadata/tables.py` — corresponding SQLAlchemy `Table` definitions
  for the three new entities. Generate Alembic migration:
  `alembic revision --autogenerate -m "Add Streaming/StreamChannel/StreamSession"`.
- `download/types.py` — add `LIVESTREAM = auto()` to `DownloadType`
  for path/state tracking. (No `DownloadMode` change — daemon-only
  per user decision.)
- `pathio/pathio.py` — add `Livestreams/` folder under the creator's
  base path; map `DownloadType.LIVESTREAM` to it in
  `set_create_directory_for_download`.
- `daemon/runner.py` — include the watcher task in `all_tasks`
  (`daemon/runner.py:1289`) so it participates in the orderly-shutdown
  sequence at `daemon/runner.py:1304-1321`. Add a pre-cancel step for
  active recordings: send graceful stop (`q\n` to ffmpeg stdin or
  the equivalent for the custom poller's "stop after current segment")
  with a **60s** wait — longer than the worker's 30s drain at
  `daemon/runner.py:1314` — because the recorder needs to flush
  buffered segments and finalize the MP4 moov atom before exit. The
  `await ws.stop_thread()` call (`daemon/runner.py:1321`) already has
  a 10s join timeout that's plenty for the WS thread.
- `daemon/bootstrap.py` — call
  `livestream_watcher.start(config, stop_event)` at the same point
  the buffering handler is attached (`daemon/bootstrap.py:122`). No
  WS interaction needed since detection is polling-based; the watcher
  is just a long-running asyncio task. Update `shutdown_bootstrap`
  (`daemon/bootstrap.py:215`) to stop the watcher cleanly when the
  daemon isn't running.
- `config/schema.py` + `config/fanslyconfig.py` — add
  `livestream_recording_enabled: bool = True` (opt-out, since the
  feature is silent until a tracked creator goes live) and
  `livestream_poll_interval_seconds: int = 30` (tunable cadence for
  the `followingstreams/online` poll).
- `fansly_downloader_ng.py` — call
  `start_livestream_watcher(config)` shortly after `config.setup_api()`
  returns, so the watcher is alive during batch download. Register
  cleanup in the existing exit path.
- `pyproject.toml` — no new dependencies needed (ffmpeg is already
  declared and the M3U8 pipeline is reused).

**No `api/websocket.py` refactor needed for v1** — detection is
polling-only. The "dict-of-list" handlers refactor previously
proposed is deferred to a future WS-based detection enhancement.

### Detection flow

**Single-track polling design** (verified against captured API
contract):

1. The watcher loops every `livestream_poll_interval_seconds` (default 30s):
   - Call `api.get_following_streams_online()` →
     `GET /api/v1/streaming/followingstreams/online?ngsw-bypass=true`.
   - Parse `response.streams` as `list[Account]` (each with populated
     `streaming.channel` and `streaming.channel.stream`).
   - Diff against `active_recordings: dict[creator_id, RecordingTask]`:
     - **New live** (in API, not in dict): spawn
       `asyncio.create_task(record_livestream(...))` keyed by
       `creator_id`, store the task in the dict. **Detached task
       lifecycle** — the recording is *not* a queue WorkItem; it
       runs independently of the worker loop's 30s drain.
     - **Still live** (in API, in dict): no-op; persist updated
       `viewerCount` / `title` to the DB so the metadata stays current.
     - **Ended** (in dict, not in API): signal the recording task to
       stop, await its graceful shutdown, remove from dict.
2. Latency-to-detect for go-live ≈ poll interval (default 30s);
   latency-to-detect for end-of-stream ≈ poll interval. This matches
   the existing daemon's polling tradeoffs and aligns with
   `ActivitySimulator` cadence in active state.
3. **Watcher runs at script start**, not gated behind the daemon's
   own start. Per user direction: livestream detection must work
   while the batch download is running too. The watcher is launched
   from `fansly_downloader_ng.py` right after `config.setup_api()`,
   parallel to the batch phase, and continues into daemon mode.

### Recording engine (`download/livestream.py`)

IVS is a sliding-window LL-HLS stream with a ~28-second segment buffer.
A recorder that lags more than ~28 seconds drops segments silently.
Two-tier engine mirroring `download/m3u8.py`'s VOD strategy but
specialized for live:

`async def record_livestream(config, account, channel: StreamChannel,
output_path, stop_event) -> Media`

**Master URL resolution** (mandatory first step, both tiers):

The API gives us a master URL on `playback.live-video.net` that has
no `dna` token. Fetch it once — IVS responds with a master HLS
manifest whose variant URLs point at `*.j.cloudfront.hls.live-video.net`
and carry the `?dna=<token>` query param. Pick the highest-bandwidth
variant via the existing
`download.m3u8._get_highest_quality_variant_url`. Save the resolved
variant URL (with `dna` token) for tier selection.

**Tier 1 — ffmpeg with IVS-tuned flags** (try first; least code):

```
ffmpeg -loglevel warning
  -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5
  -http_persistent 1
  -live_start_index 0
  -i <variant_url_with_dna_token>
  -c copy -movflags +faststart -f mp4
  <output_path>
```

ffmpeg propagates query strings to segment requests, so the `dna`
token is preserved automatically. On ffmpeg crash / repeated
segment 404s, fall through to tier 2.

**Tier 2 — custom sliding-window poller** (robust fallback for
when ffmpeg drops segments under load):

- Modeled on `download/m3u8.py::_try_segment_download` but with a
  **polling loop** instead of one-shot `fetch_m3u8_segment_playlist`.
- Re-fetch the variant playlist every ~1 second
  (`TARGETDURATION ÷ 2`).
- Track the last-seen `EXT-X-MEDIA-SEQUENCE`. New segments are those
  with a sequence number greater than the last-seen value.
- Download new segments concurrently
  (`concurrent.futures.ThreadPoolExecutor`, the same primitive the
  existing tier-3 segment downloader uses at line 642).
- Append each `.ts` segment to a single MP4 via PyAV concat — the
  existing `_mux_segments_with_pyav` works on a list of `.ts` Path
  objects and is reusable.
- Skip `EXT-X-PREFETCH` entries — they're LL-HLS hint segments not
  yet fully published.
- **Auth model**: do NOT call
  `download.m3u8.get_m3u8_cookies` (that's the CloudFront-cookie
  path used for VOD). IVS segment URLs carry their own `?dna=`
  token. Just use
  `config.get_api().get_with_ngsw(url=segment_uri, add_fansly_headers=False)`
  — `get_with_ngsw` preserves the query string verbatim.
- If the manifest hasn't advanced (`MEDIA-SEQUENCE` unchanged) for
  3 × `TARGETDURATION` seconds, treat it as end-of-stream and exit
  the loop.

**End-of-recording signals** (whichever fires first):

1. The watcher's poll detects the creator dropped from
   `followingstreams/online` → sets `stop_event` for this recording.
2. ffmpeg subprocess exits on its own (manifest 404 / acquires
   `EXT-X-ENDLIST`) — tier 1 only.
3. Manifest hasn't advanced for 3 × `TARGETDURATION` (≈6s at
   default IVS LL-HLS) — tier 2 only.
4. App shutdown (Ctrl-C → daemon stop_event → recorder sends `q\n`
   to ffmpeg stdin / sets internal stop flag for tier 2; finalize
   within 30s, SIGTERM-then-SIGKILL after 60s).

**Post-record**: hash via `fileio.fnmanip.get_hash_for_other_content`,
persist via `metadata.process_media_download`. The recorded MP4 is a
new `Media` row with mimetype `video/mp4`, joined to the `Account`,
and a `MediaLocation` row with `locationId=1` (Direct) pointing at
the local file path. The associated `StreamSession` row already
exists (saved when the watcher first detected the live broadcast),
linking the recording to its stream metadata (title, startedAt,
viewerCount-at-discovery).

### Chat sidecar (`daemon/livestream_chat.py`)

- `async def capture_chat(config, chat_room_id, output_path, stop_event)`
- Open a separate `FanslyWebSocket(base_url="wss://chatws.fansly.com")`
  with the existing infrastructure.
- Send the chat-room-join handshake (`MSG_CHAT_ROOM = 46001`) using
  the captured `chat_room_id`.
- Each svc=46 event → append a JSONL row to
  `<recording_basename>.chat.jsonl`. Schema:
  `{ts, sender_account_id, username, displayname, content,
    is_creator, raw_event}`.
- Closes the WS when `stop_event` fires or the recording task exits.

### Critical-files-to-read for the implementer

| File | Why |
|------|-----|
| `daemon/bootstrap.py:81-220` | Where `start_livestream_watcher` slots in alongside the buffer handler |
| `daemon/runner.py:1289-1321` | The task list + shutdown sequence the watcher joins |
| `download/m3u8.py:70-161` | `_get_highest_quality_variant_url` + `fetch_m3u8_segment_playlist` (master-URL resolution path to reuse, then bypass the VOD-only check at line 156) |
| `download/m3u8.py:560-682` | `_try_segment_download` (the structural template for the tier-2 sliding-window poller) |
| `metadata/models.py:1085-1100, 1913-1972` | `MediaLocation` + `Account` definition for migration |
| `api/fansly.py:299-376` | Existing endpoint method patterns (`get_creator_account_info`, `get_account_media`) — template for the two new streaming endpoints |
| `api/rate_limiter.py` | Confirm the watcher's poll obeys the global rate limiter (it should — `get_with_ngsw` already integrates) |
| `~/.claude/projects/.../memory/project_livestream_ivs.md` | Verified API contract + IVS-specific notes |

### Existing functions/utilities to reuse

- `download.m3u8._get_highest_quality_variant_url(config, url, cookies)`
  — picks the best variant from a master playlist. Pass `cookies={}`
  for IVS (auth is in the URL `?dna=` token, not cookies).
- `download.m3u8._mux_segments_with_pyav` — muxes a list of `.ts`
  Path objects into MP4. The tier-2 sliding-window poller writes
  segments to a temp dir and feeds them through this.
- `config.get_api().get_with_ngsw(url, ...)` — HTTP fetch that
  preserves query strings (so the `?dna=` token rides along) and
  obeys the global rate limiter.
- `metadata.process_media_download(config, state, media)` — persist
  recorded Media row + dedup against existing hashes.
- `fileio.fnmanip.get_hash_for_other_content(path)` — post-record
  hash of the finalized MP4.
- `pathio.set_create_directory_for_download` /
  `pathio.get_media_save_path` — path construction; just add the
  `LIVESTREAM` branch.

**Do NOT use `download.m3u8.get_m3u8_cookies`** — that's the
CloudFront-signed-cookie path used for VOD content, and IVS uses
`?dna=` URL tokens instead. Calling it on an IVS URL would produce
empty cookies (no `Policy`/`Signature`/`Key-Pair-Id` query params)
and waste a request.

## Verification

End-to-end:

1. `poetry install` (no new deps).
2. `alembic upgrade head` to apply the
   `Streaming`/`StreamChannel`/`StreamSession` migration.
3. Save the captured 2026-05-03 API responses as fixtures in
   `tests/json/livestream/`.
4. Run the downloader against a known-live creator:
   `poetry run python fansly_downloader_ng.py -u <creator> --daemon`.
   Verify within ~30s the watcher logs
   `livestream: detected go-live for <creator>` and a recording
   subprocess starts. Tail `<creator>_fansly/Livestreams/<title>_<ts>.mp4`
   to confirm it's growing.
5. Stop with Ctrl-C → confirm the recorder finalizes the MP4
   gracefully, the file plays in `mpv` / QuickTime, and the
   sidecar `.chat.jsonl` parses as line-delimited JSON.
6. `pytest tests/daemon/unit/test_livestream_watcher.py
   tests/download/unit/test_livestream.py -v` — unit coverage.
7. `pytest -n8 --cov` for the full suite — confirm no regressions in
   existing m3u8 tests (the changes are largely additive: new files
   plus small additions to `metadata/models.py`, `metadata/tables.py`,
   `daemon/runner.py`, `daemon/bootstrap.py`,
   `fansly_downloader_ng.py`, and the `download/types.py` enum).
8. `pre-commit run --all-files` for ruff/mypy/bandit pass.

## Out of scope for v1 (known follow-ups)

- **WebSocket-based detection (svc=45)**. v1 uses the
  `followingstreams/online` poll endpoint with ~30s latency. Real-time
  WS-event detection would shave the latency to seconds but requires
  reverse-engineering the svc=45 payload + the
  `_event_handlers: dict[int, Callable]` → `dict[int, list[Callable]]`
  refactor in `api/websocket.py:160`. Defer until polling proves
  insufficient.
- **Disk-space management.** Long broadcasts can be many GB. v1
  records to whatever the path resolves to; a `--max-recording-size`
  guard or rotation policy is a separate task.
- **Concurrency cap.** If 5 creators go live simultaneously, v1
  records all 5. A `livestream_max_concurrent_recordings` config
  is a follow-up.
- **Chat-to-DB.** User chose JSONL sidecar. Persisting chat to a
  `LivestreamChatMessage` table for joinable queries / Stash export
  is deferred.
- **`-dm livestream` CLI mode.** User chose daemon-only. A
  one-shot manual mode is a future addition.
- **`permissionFlags` modeling.** v1 captures the
  `streaming.channel.stream` fields but ignores
  `permissions.permissionFlags` (subscription/tip gates). If the
  watcher should skip recordings the user can't access, decode the
  flags before spawning the recorder.
- **EXT-X-PREFETCH (LL-HLS hint segments)**. v1 ignores prefetch
  entries — they're 1-2 segments not yet fully published. Honoring
  them would shave ~2 seconds off the recording's tail latency.
- **`dna` token refresh**. v1 assumes the `dna` token is valid for
  the entire broadcast. If production shows the token expiring
  mid-stream, the recorder needs to re-fetch the master URL from
  `playbackUrl` and swap the variant URL on token-401.
- **Discovery-feed recording**. v1 only records creators in the
  `followingstreams/online` list (followed creators).
  `livesuggestions` is informational; recording random discovery
  feed creators would require an opt-in config flag and stricter
  concurrency guards.
