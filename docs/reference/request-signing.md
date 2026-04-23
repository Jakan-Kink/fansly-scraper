---
status: current
---

# Fansly REST API Request Signing

Every authenticated REST API call to `apiv3.fansly.com` carries four
anti-detection headers added by an HTTP interceptor on the Fansly
frontend. A client that omits this bundle is fingerprint-distinguishable
from a real browser at the HTTP layer. The downloader replicates the
same set in `api/fansly.py`.

## Header bundle

| Header                | Value                                                                  | Derivation                                                                          |
| --------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `fansly-client-id`    | Opaque client instance identifier                                      | Generated per-session by the frontend; stable for the tab's lifetime                |
| `fansly-client-ts`    | Current local time plus `backendServerDateMsOffset_` (server − local)  | Synced from the versioning service's `getServerInfo().serverTime` on each request   |
| `fansly-session-id`   | The `sessionId` from the decoded auth token                            | `base64_decode(authorization_token).split(":")[0]`                                  |
| `fansly-client-check` | Hex-encoded digest over the URL path                                   | See [Digest formula](#digest-formula) below                                         |

## Digest formula

The `fansly-client-check` header is computed by the interceptor as:

```
hash(this.checkKey_ + "_" + URL(url).pathname + "_" + clientId)
```

- `this.checkKey_` — a rotating secret string embedded in the frontend
  bundle; extraction is covered in [CHECKKEY_JSPYBRIDGE.md](CHECKKEY_JSPYBRIDGE.md).
- `pathname` — URL path only, no query string, no host.
- `clientId` — the same value sent as `fansly-client-id`.
- The hash is a non-cryptographic 53-bit hash (cyrb53), hex-encoded via
  `.toString(16)`.
- Result is cached per-pathname, so repeated calls to the same endpoint
  don't re-hash.

`fansly-session-id` is **not** part of the digest input — it's a
standalone header, separate from the signed bundle. The digest binds
`(checkKey, pathname, clientId)`, nothing else.

## Grepping the frontend bundle

Bundle filenames are content-hashed (`main.<hash>.js`) and rotate on
every Fansly deploy, so filenames and line numbers are not stable
references. What survives re-bundling:

- **String literals** — `"fansly-client-id"`, `"fansly-client-ts"`,
  `"fansly-session-id"`, `"fansly-client-check"`. Minifiers don't
  rewrite string contents.
- **Private field** — `this.checkKey_`. The trailing-underscore
  convention is load-bearing for property access, so it survives
  minification.

Grep any current bundle (beautified or not) for those anchors to locate
the interceptor. Do not cite line numbers from a beautified local copy
in docs — the beautifier's line numbering is local and meaningless
across copies.

## Downloader implementation

`session_id` is derived from the auth token during login: the
`authorization_token` config value base64-decodes to
`<sessionId>:1:2:<hash>`, and the first colon-separated field is the
session ID. The `f-s-c` cookie independently contains
`<sessionId>:1:1:<hash>` — same session ID, different type code and
hash.

The session-id header is attached in `api/fansly.py`:

```python
if self.session_id != "null":
    fansly_headers["fansly-session-id"] = self.session_id
```

Session ID is **internal only** — there is no user-facing CLI flag or
config field for it. A dead `--session-id` argparse stub was reaped
from `config/args.py` in commit `31285f3ff` (alongside
`validate_adjust_session_id` in `validation.py`). Users provide only
`authorization_token`; `session_id`, the `f-s-c` cookie, and all four
signing headers are derived or computed internally.

Check-key extraction is handled by `helpers/checkkey.py` (see
[CHECKKEY_JSPYBRIDGE.md](CHECKKEY_JSPYBRIDGE.md)); the digest
computation uses the extracted value according to the formula above.

## Why it matters

Sending only `Authorization` (the bearer token) while omitting the
`fansly-*` headers is trivially distinguishable from real frontend
traffic at the HTTP layer. Matching the complete header set is the
baseline for blending in with normal browser activity. The WebSocket
endpoint has its own parallel anti-detection considerations — see
[Fansly-WebSocket-Protocol.md § Anti-Detection Notes](Fansly-WebSocket-Protocol.md#anti-detection-notes).
