---
title: Manual Token Extraction
status: current
---

# Manual Token Extraction

Fansly Downloader NG's **recommended** authentication path is automatic
browser-token extraction via the `browser-auth` Poetry group — see the
[README Setup section](https://github.com/Jakan-Kink/fansly-scraper/blob/main/README.md#%EF%B8%8F-setup)
for the `poetry install --no-root --with browser-auth` flow. It supports Chrome,
Firefox, Microsoft Edge, Brave, Opera, and Opera GX, handling everything
for you.

This page covers the **manual fallback** for cases where automatic
extraction isn't available:

- You use Safari (not supported by `browser-auth`)
- You're running on a headless server / VM without a GUI browser
- The `plyvel-ci` install failed on your platform
- You just want to inspect the values yourself before handing them to the
  downloader

> **Make sure you are logged into [fansly.com](https://fansly.com) in your
> browser before starting.** Both methods below read the auth material from
> the active browser session.

## Option 1 — DevTools Console (recommended fallback)

The fastest manual path: paste a tiny JavaScript snippet into the browser
console and have it print the two values you need.

1. Open any Fansly creator's page (or [fansly.com](https://fansly.com)).
2. Open the browser DevTools — usually `F12`, or right-click → **Inspect**.
3. Switch to the **Console** tab.
4. Paste this snippet and press Enter:

   ```javascript
   console.clear();
   const activeSession = localStorage.getItem("session_active_session");
   const { token } = JSON.parse(activeSession);
   console.log("%c➡ authorization_token =", "color: limegreen; font-weight: bold;", token);
   console.log("%c➡ user_agent =", "color: yellow; font-weight: bold;", navigator.userAgent);
   ```

5. The console now prints your `authorization_token` and `user_agent`.
   Copy both values.
6. Open `config.yaml` in the downloader's working directory and paste the
   values into the `my_account` section:

   ```yaml
   my_account:
     authorization_token: PASTE_TOKEN_HERE
     user_agent: PASTE_USER_AGENT_HERE
   ```

7. Save the file and run the downloader.

## Option 2 — DevTools Network Tab

Useful when you want to verify the token comes from a real API call rather
than `localStorage`, or if the site has moved away from `session_active_session`
in the future.

1. Open Fansly and DevTools (`F12`).
2. Switch to the **Network** tab.
3. Reload the page (`F5` / `Ctrl+R`). Network activity populates the list.
4. Filter the list to **Fetch/XHR** and pick any request to `apiv3.fansly.com`.
5. In the **Headers** panel, copy:
   - `authorization:` → `authorization_token` in `config.yaml`
   - `user-agent:` → `user_agent` in `config.yaml`

Paste both into the `my_account` section of `config.yaml` as shown in
Option 1 step 6.

## Verifying the values

Token values are 50–70 characters of alphanumerics. If yours looks much
shorter, you probably grabbed the wrong header (some requests send a
short session cookie instead of the auth JWT). Use Option 2 and pick a
different request.

The `user_agent` value will be 80–200 characters describing your browser
and platform (e.g., `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...`).
If you see a shorter value, you likely copied the wrong string.

## Keeping the token fresh

Tokens rotate on Fansly's side. If the downloader starts returning `401`
or `authentication failed` errors, re-run one of the two methods above
and update `config.yaml` — the next run will pick up the new value.

---

*Historical note: the JavaScript snippet above was originally documented
by [@prof79](https://github.com/prof79) on the upstream project wiki.
It has been updated for the YAML-based configuration introduced in
v0.12/v0.13.*
