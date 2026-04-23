---
status: current
---

# Monitoring Daemon Cadence & Anti-Detection Reference

Reference data for the post-batch monitoring daemon (v0.13+). Intervals
below are derived from analysis of Fansly's own frontend code in `main.js`
— the daemon matches these cadences to minimize detection surface.

See [`docs/planning/monitoring-daemon-architecture.md`](../planning/monitoring-daemon-architecture.md)
for the overall daemon architecture. This page documents the *why* behind
the intervals and the signals the daemon uses (and deliberately avoids).

## Polling Interval Reference

Fansly's frontend uses a three-tier visibility model (active tab, idle,
hidden) with different poll cadences for each resource. The daemon matches
these intervals so its traffic blends with real browser sessions.

| Resource           | Active interval      | Idle interval           | Idle threshold     |
| ------------------ | -------------------- | ----------------------- | ------------------ |
| Home timeline      | 3 min + 0–10s jitter | 10 min + jitter         | > 30s no input     |
| Story states       | 30 sec               | 5 min                   | > 5 min no input   |
| Live streams       | 60 sec               | 5 min                   | > 5 min no input   |
| FYP preview        | 10 min               | *never* (stops)         | > 30s no input     |
| WebSocket ping     | 20–25 sec (always)   | 20–25 sec (always)      | N/A                |
| Notification check | on tab-unhide > 30s  | —                       | —                  |

### Tab visibility behavior (browser)

When the real Fansly tab becomes hidden, **all** polling stops (timeline,
stories, live streams, FYP). On return to visibility:

- **> 1 second hidden** → check for updates + `assertWebsocketConnection()` (reconnect WS)
- **> 30 seconds hidden** → recheck notifications + reload wallet balances
- **> 6 hours hidden** (216e5 ms) → force PWA update (full page reload)

### Anti-detection considerations for the daemon

The daemon runs headless — there is no tab to hide. To blend in:

- **Add jitter** to all poll intervals (match the 0–10s jitter on home timeline)
- **Match the 3-min active / 10-min idle cadence** from the real frontend
- **Correlate API requests with WS ping activity** — both should be active simultaneously
- **On "tab unhide" equivalent** (e.g., after a pause), call
  `assertWebsocketConnection()` before polling. This matches browser behavior.
- **Wallet transaction types 6002 and 6515** are filtered from UI alerts
  (internal transfers) — if the daemon surfaces alerts, filter these too.
- **"STOP" console warning** is emitted at app init on the real frontend.
  Headless sessions never trigger this, which is a fingerprint — not
  something the daemon can replicate, but good to be aware of.

## Skipping Inactive Creators

The `should_process_creator()` filter in `daemon/filters.py` short-circuits
creators who haven't posted since the last daemon tick. The signal it uses
is **`post.createdAt` from the first timeline page**, compared against
`MonitorState.lastCheckedAt`.

The filter **specifically rejects two alternative signals**. Both were
investigated and discarded on 2026-04-17; the rationale is retained
below so future work doesn't relitigate this decision.

### Rejected signal: `Account.lastSeenAt`

Per `main.js` (line 16014):

```javascript
t.lastSeenAt = e.lastSeenAt || 0
```

The `|| 0` means creators with last-seen visibility **turned off** return
`0` forever. Compared against `MonitorState.lastCheckedAt`, that resolves
as 1970-01-01, which is always older than any realistic "last checked"
timestamp — so these creators get **stuck-skipped indefinitely**.

Privacy-opt-out combined with this default-to-zero fallback makes
`lastSeenAt` unreliable as a "did this creator do something since we
last checked?" signal.

### Rejected signal: `TimelineStats.fetchedAt`

Empirical evidence from a real observation:

- `fetchedAt = 1774849061652` (ms) = **2026-04-27**
- Creator's most recent `post.createdAt = 1774783936` (seconds) = **2026-04-26**
- The `fetchedAt` appeared ~18 hours **after** the latest real post,
  with no new content in between.

Further investigation of `main.js` confirms the Fansly client **never
reads `timelineStats.fetchedAt`** for any purpose — it's server-side
cache-regeneration metadata, not a "time of last new post" timestamp.

### Why there's no `lastPostedAt` equivalent

Searches across `main.js` for `lastPosted`, `latestPost`, `postCount`,
and `mostRecent` returned zero hits. If Fansly's backend tracked "when
did this creator last post?" anywhere, the client UI would surface it
somewhere. It doesn't — which means the data point doesn't exist on
the server, and the downloader has to derive it from the timeline itself.

### The working signal: first-page timeline probe

The daemon's `should_process_creator()`:

1. Fetches the creator's first timeline page (one API call)
2. Filters out pinned posts (creators can re-pin old posts at any time,
   so a pinned post's `createdAt` tells us nothing about new activity)
3. Compares the most recent **non-pinned** `post.createdAt` against
   `MonitorState.lastCheckedAt`
4. Returns `True` if the post is newer, `False` otherwise

Session baseline overrides available via CLI:

- `--monitor-since <iso>` — precise window override
- `--full-pass` — sets baseline to `2000-01-01T00:00:00Z`, forcing
  re-check of every creator

## Optimization Layers

Three layers of optimization reduce the number of per-creator calls the
daemon makes on each pass:

### 1. Home timeline diffing

`GET /timeline/home?before=0&after=0&mode=0` returns recent posts from
**all** followed creators in a single call. Comparing returned post IDs
against last-seen identifies which specific creators have new content.
The daemon only descends into per-creator timelines for those creators.

### 2. Story state flip detection

`GET /mediastories/following?limit=100&offset=0` returns
`MediaStoryState.hasActiveStories` per creator. The daemon only calls
`mediastoriesnew` for creators whose `hasActiveStories` flipped to true
since the last tick (`MonitorState.lastHasActiveStories`).

### 3. Timeline-probe creator skip (detailed above)

Last resort for creators the upstream optimizations didn't cover.

## API Endpoint Reference

Fansly API endpoints relevant to the daemon:

| Endpoint                                       | Purpose                                      |
| ---------------------------------------------- | -------------------------------------------- |
| `GET /timeline/home?before=0&after=0&mode=0`   | Home timeline — all followed creators at once |
| `GET /timeline/{accountId}`                    | Per-creator timeline (for skip probe + downloads) |
| `GET /mediastories/following?limit=100&offset=0` | Story states for all followed creators     |
| `GET /mediastoriesnew?accountId={id}`          | New stories for a specific creator           |
| `GET /account/{id}/following`                  | Your following list — NO `lastSeenAfter` param |
| `GET /account/{id}/followersnew`               | Followers list — HAS `lastSeenAfter` filter (not used by daemon) |

**Client-side filtering**: the `/following` endpoint does NOT support
server-side `lastSeenAfter` filtering (unlike `/followersnew`). If the
daemon ever needs to filter the following list by last-seen, it must do
it in-process.

## Timeline Refresh Semantics

Two refresh modes are defined in `main.js`:

- **`refreshTimeline`** — incremental fetch of new posts only. **The daemon uses this.**
- **`reloadTimeline`** — full reload from scratch. The daemon avoids this.

Additional rules from `main.js`:

- **Active user**: refreshTimeline every 3 min + jitter
- **Idle user**: refreshTimeline every 10 min + jitter
- **Tab hidden**: STOP polling entirely (visibility API)
- **Tab unhidden after > 3 hours**: force refresh (daemon mirrors this with restart-pass)
- **Scrolled to top + stale > 5 min**: reload instead of refresh (not applicable to daemon — no scroll state)
