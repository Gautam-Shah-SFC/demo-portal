# Demo Portal — Website Plan

This is a handoff spec for building the Demo Portal website that talks to the endpoint agent (this repo). It covers the two pages that reflect what's **actually implemented today**: **Provider Management** (add/remove which browsers and IDEs a device monitors) and **Activity Logs** (view what's been captured).

**Scope note (read this first)**: an earlier draft of this doc described a third page ("Review Queue") and a `network_flag`/`record_type` mechanism for detecting *unknown* AI platforms via network-level telemetry. That's the "Tier 2" idea from `modification_needed.md` — it was discussed and deliberately **deferred as a separate, larger initiative** (new native dependency, likely elevated-privilege requirement, not yet built). The agent has no network watcher, no `NetworkFlagEvent`, and never sends a `record_type` field. Don't build Page 3 or anything referencing it yet — see "Future (not yet built)" at the bottom for that design, kept for reference only.

**Architecture change (read this too)**: the config-push direction (Provider Management → device) has moved from an inbound HTTP webhook to an **outbound WebSocket connection initiated by the device**. The old model required port 8765 reachable on every device (one ngrok tunnel per device, URL changing on every restart) — the new model needs the device to reach the portal, exactly like the ingest direction already does, and nothing more. See "Page 1" below for the full contract. **The ingest HTTP endpoint (`/api/ingest/activity`) and its FastAPI companion process are unchanged by this** — same envelope, same full-replace semantics, same everything; only the portal→agent transport changed, not agent→portal.

## Tech stack for this build: Streamlit (for now)

Use **Streamlit** (Python) to build this. A few things worth planning around given Streamlit's specific model, so this doesn't turn into a surprise partway through:

- **Streamlit is a reactive dashboard framework, not a general web server** — the whole script re-runs top-to-bottom on every user interaction. Use `st.session_state` for anything that needs to persist across reruns (login state, which device is selected, etc.), and Streamlit's built-in multi-page support (a `pages/` directory, one file per page — `pages/1_Provider_Management.py`, `pages/2_Activity_Logs.py`, etc.) for the Page 0/1/2 structure this doc describes.
- **⚠️ Neither the ingest endpoint NOR the new config-push WebSocket fits naturally into Streamlit.** Streamlit has no built-in way to expose a plain `POST /api/ingest/activity` route, and no way to terminate a WebSocket connection either (no `@app.websocket(...)`-equivalent). Both need to be handled by a **separate, small companion process**:
  - Run a minimal **FastAPI** app alongside Streamlit, on a different port (`uvicorn`), exposing (a) the ingest POST route (write incoming records to a shared SQLite file or CSV) and (b) a WebSocket route (`@app.websocket("/ws/agent")`) that devices connect to for config-push — see Page 1 below for that contract. Point the agent's `backend_url` and `ws_url` at *that* process, not at Streamlit itself.
  - The Streamlit app's Activity Logs page then just reads from that same shared store on each rerun (or on a timer — see below) — it never receives the POSTs directly. The Provider Management page similarly never opens the WebSocket itself — it needs the FastAPI process to hold the live device connections and expose *some* way for Streamlit to trigger a push through it (simplest: Streamlit also just writes "desired state per device" to the same shared store, and the FastAPI process's WS handler reads from there when deciding what to push — avoids Streamlit and FastAPI needing to share in-memory Python objects across processes).
  - This is the one piece of real architecture this demo needs beyond "just Streamlit," and it's worth deciding early rather than discovering it partway through building Page 1 or 2.
- **Auto-refreshing the Activity Logs page** (for the "keeps counting up" relative-timestamp requirement, and for new events showing up without a manual reload) needs either the `streamlit-autorefresh` package or a manual "Refresh" button — Streamlit doesn't push updates to an open browser tab on its own.
- Suggested minimal dependency list: `streamlit`, `pandas` (for the logs table), `requests` (for calling the agent's webhook from the portal side), plus `fastapi` + `uvicorn` for the companion ingest listener.

## The two directions of communication

There are two completely separate API calls involved, going in opposite directions. Don't confuse them:

1. **Agent → Portal** (already working today): the agent ships captured events *to* the portal's ingest endpoint (the companion FastAPI process above). This is what feeds the **Logs page**.
2. **Portal → Agent, over a connection the AGENT opens** (built, needs a UI): every device opens an outbound WebSocket connection to the portal (`wss://<portal>/ws/agent`) and keeps it alive. Pushing a config change means the portal finds that device's already-open connection and sends a message down it — the portal never dials into the device. This is **not** a natural fit for a simple Streamlit button click the way a `requests.post()` to a device URL was — the live WS connections are held by the companion FastAPI process, not by Streamlit, so the Provider Management page's "Save" action needs to get its request to that process somehow (see the tech-stack note above for the simplest option: a shared store both processes read/write).

---

## Page 0: Login & Token Issuance (prerequisite for everything else)

### Why this is needed
The JWT currently hardcoded into `agent_config.yaml` was cut from the **production** retroper backend — it has nothing to do with this demo portal, and it's the only token that exists anywhere right now. Reusing it here would mean the demo has no auth of its own, and no way to tell whose test device is whose. The demo portal needs its own small, self-contained login system, entirely separate from production.

### Flow for this demo (kept intentionally simple)
1. Create one demo user — e.g. a "John Doe" user with an email + password. **No database needed for this** — a plain CSV file (email, password/hash, maybe a display name) is enough for a demo with one or a handful of users.
2. That user logs into the portal with email/password, checked against the CSV. In Streamlit terms: a simple form on the login page, storing `st.session_state["logged_in_user"]` on success, checked at the top of every other page (redirect/stop if not set).
3. On successful login, the portal issues a token for that user — a JWT is fine, or even a simple opaque token; it does **not** need to match production's claim shape, since this system is fully independent of production.
4. The portal displays that token (e.g. a "Device Token" screen with copy button) so it can be pasted into `agent_config.yaml`'s `auth_header_value` (`Bearer <token>`) before that test device's exe is built/packaged.
5. From then on, that token is what:
   - the exe sends outbound on every upload to the demo ingest endpoint (`Authorization` header — already exactly how `auth_header_value` works, no agent-side change needed for this).
   - the agent sends as the `Authorization` header on its outbound WebSocket connection attempt, and the portal must check for that same header/value when accepting that device's WS upgrade request — see the updated auth note in Page 1 below.

### Scope for now
One demo user (e.g. John Doe), stored in a CSV, is enough to start: log in once, get a token, bake that token into one test exe, and verify the whole loop end to end before worrying about a real user database, multiple users, or multiple devices.

---

## Page 1: Provider Management (add/remove browsers & IDEs)

### What it does
Lets an admin pick which AI platforms a given device should monitor, and push that choice live to the running agent — no reinstall, no rebuild, takes effect within a second.

### The six providers
| Provider key  | Type    | Display label | Matched by (browser entries) |
|---------------|---------|---------------|-------------------------------|
| `chatgpt`     | browser | ChatGPT       | domain: `chatgpt.com` |
| `claude`      | browser | Claude        | domain: `claude.ai` |
| `gemini`      | browser | Gemini        | domain: `gemini.google.com` |
| `cursor`      | IDE     | Cursor        | process name (title unrestricted) |
| `vscode`      | IDE     | VS Code       | process name (title unrestricted) |
| `antigravity` | IDE     | Antigravity IDE | window title |

These are the only values the `providers` field recognizes. Anything else in `providers` is silently ignored — but see `custom_apps` below for adding platforms that aren't in this fixed list.

**Browser providers are matched by domain now, not window title** — the agent reads the real address-bar hostname via UI Automation rather than guessing from the page title. This is internal to how the shorthand expands; the portal doesn't need to do anything differently for the six fixed checkboxes, this only matters for `custom_apps` below. (See `docs/domain_vs_title_matching.md` in this repo for the full before/after if useful background.)

A platform added via `custom_apps` gets its **response** captured correctly too, not just the prompt, and gets labeled with its real name (derived from the matched domain, or the title keyword for non-browser entries) instead of a generic "Custom AI" fallback. No portal-side change needed to benefit from this — it's just better data arriving in fields you already display (see Page 2, Record shape A).

### Adding a platform/IDE that isn't one of the six
A second, optional field — `custom_apps` — accepts raw entries for **anything else**: a new AI website (on an existing browser), or a brand-new IDE/app entirely. No agent code change or rebuild is ever needed for this.

For a **browser** `process_name` (`chrome.exe`/`msedge.exe`/`brave.exe`/`opera.exe`): use `domain_pattern` — a regex matched against the hostname only (path/query ignored, so `"grok\\.com"` matches `grok.com/anything?query=1` automatically). `window_title_pattern` is still accepted on a browser entry as a legacy option, but `domain_pattern` is preferred and is what the UI should produce for new entries.

For any other `process_name` (an IDE/desktop app): use `window_title_pattern` exactly as before — domain matching has no meaning for a non-browser process.

```json
{
  "providers": ["chatgpt"],
  "custom_apps": [
    {"process_name": "chrome.exe", "domain_pattern": "grok\\.com"},
    {"process_name": "chrome.exe", "domain_pattern": "perplexity\\.ai"},
    {"process_name": "Windsurf.exe", "window_title_pattern": ""}
  ]
}
```
- Same **full-replace** rule as `providers` (see below) — `custom_apps` must include every custom entry that should stay active, every call.
- Invalid entries get the whole request rejected with `400` and a message identifying which entry — see the Responses table below, including domain-specific validation cases.
- **UI**: the "add a custom platform" form should ask whether the target is a browser site or a desktop/IDE app (or infer it from whether the process name is one of the four known browser exes), showing a domain input (placeholder: `example.com`) for the browser case, or a window-title-keyword input for the desktop/IDE case.

### ⚠️ Custom platforms need to be toggleable too, not just one-shot
The agent's API treats `custom_apps` as a plain list every time — it has no memory of what was pushed last (see "no `GET /status`" below), so **the portal is the thing that has to remember** which custom platforms have been defined for a device, or every toggle would require the admin to retype the platform's config from scratch just to turn it back on.

Concretely, the portal's device registry needs to store, per device, a **list of defined custom platforms**:
```
device: "Test Device 1"
custom_platforms:
  - {label: "Grok", process_name: "chrome.exe", domain_pattern: "grok\\.com", enabled: true}
  - {label: "Perplexity", process_name: "chrome.exe", domain_pattern: "perplexity\\.ai", enabled: false}
```
- **Adding a new custom platform** creates a new entry in this list for that device, `enabled: true` by default, and immediately sends a `publish` message including it down that device's live connection.
- **Every subsequent visit to the page** renders one checkbox per entry in this list, in addition to the 6 fixed ones.
- **Toggling any checkbox and hitting Save** recomputes the full `providers` list and the full `custom_apps` list (pulled from each *checked* custom entry's stored definition) and sends both in one `publish` message — no new agent-side capability needed, purely how the portal builds the message from its own remembered state.
- Unchecking a custom platform just means the next `publish` message omits it — but keep its definition in the registry so it can be re-checked later without redefining it.

### UI
- One row/card per device (see "Device registry" below), showing 6 checkboxes/toggles for the known providers, plus one checkbox per custom platform already defined for that device, plus an "add custom platform/IDE" form to define a new one.
- A **Save** (or **Apply**) button per device that sends the *complete* checked set in one call — both `providers` and `custom_apps`.
- Show the response as a success toast (`active_providers` + `active_custom_apps` echoed back) or an error banner (see error cases below).

### ⚠️ Critical semantic: full replace, not add/remove
Despite the page being framed as "add or remove," the underlying API call is **not incremental**. Every call must include the **entire desired set** of active providers, not just the one being toggled. If the device currently has `["chatgpt", "cursor"]` active and the admin unchecks nothing but adds Gemini, the call must send `["chatgpt", "cursor", "gemini"]` — sending just `["gemini"]` would turn ChatGPT and Cursor **off**. The frontend must always compute and send the full checkbox state, never a delta.

### The WebSocket contract (replaces the old `/publish` HTTP call)

Every device connects **out** to `wss://<portal-host>/ws/agent` (this route must live in the companion FastAPI process — see the tech-stack note above — and stays open for as long as the agent runs, reconnecting with backoff if it drops). There is no more `/initialize` vs `/publish` distinction — both collapsed into a single message type once there's no separate path to disambiguate them by.

**Auth happens once, at connection time**, not per message: the portal's WS route handler must read the `Authorization` header off the WebSocket upgrade request and reject the upgrade (close/refuse before accepting) if it's missing or doesn't match that device's expected token — the same shared-token model as before, just checked at handshake instead of per-POST. One real behavioral difference worth knowing: revoking a token no longer takes effect instantly for an already-connected device — it only takes effect the next time that device reconnects (with the now-invalid token, and gets rejected then). If instant revocation matters, the portal has to actively close that device's live connection when a token is revoked, not just wait for it to notice.

Every JSON message, in both directions, carries a `"type"` field:

**`hello`** (agent → portal, sent once immediately after connecting, before anything else):
```json
{
  "type": "hello",
  "device_id": "<a stable per-device identifier, unique to that install>",
  "monitored_apps_hash": "<sha256 of that device's current monitored_apps.json bytes, or null if it doesn't have one yet>",
  "connected_at": "<ISO-8601 UTC timestamp>"
}
```
Use this to detect drift: remember the hash you last intentionally pushed to this `device_id`; if a device's `hello` shows a different hash (or the device reconnects after being offline for a while), that config push likely never landed — re-send it.

**`publish`** (portal → agent — send this whenever "Save" is clicked, and also as the very first push to a brand-new device; there's no separate "first time" message anymore):
```json
{
  "type": "publish",
  "request_id": "<any string you want echoed back, optional>",
  "providers": ["chatgpt", "gemini", "cursor"],
  "custom_apps": [
    {"process_name": "chrome.exe", "domain_pattern": "grok\\.com"}
  ]
}
```
Same full-replace semantics as before (see the warning above) — this doesn't change just because the connection is now persistent.

**`publish_result`** (agent → portal, the response to a `publish`):
```json
{"type": "publish_result", "request_id": "<echoed back>", "status": "ok", "active_providers": [...], "active_custom_apps": [...]}
```
or on a validation/apply failure:
```json
{"type": "publish_result", "request_id": "<echoed back>", "status": "error", "message": "..."}
```
`message` identifies which field/entry was bad, same validation rules as before (missing/wrong-typed `providers`; a `custom_apps` entry missing/bad `process_name`; for a browser `process_name`, neither `domain_pattern` nor `window_title_pattern` provided, or `domain_pattern` isn't a valid regex; for a non-browser `process_name`, invalid `window_title_pattern`).

Any message `type` your portal doesn't recognize gets logged and ignored by the agent, not treated as an error — so it's safe to add new message types later without needing every device rebuilt first.

**If the device isn't currently connected** (offline, agent not running, network issue), there's no live socket to send a `publish` down — this is the WS-era equivalent of the old "no response / timeout" case. Treat it the same way: the portal should remember the desired state and either wait for the device's next `hello` to detect the drift (see above) and re-push then, or just show "device offline" and let the admin retry later.

### Device registry — now a *connection* registry, not a URL registry
The agent still has no separate enrollment step — a device just shows up the first time it connects. The portal needs a table keyed by `device_id` (from `hello`) tracking: is this device *currently connected* (a live WS handle the FastAPI process holds in memory — if that process ever runs multiple worker processes, know that "which worker holds which device's socket" becomes a real problem needing sticky routing or a shared store like Redis; not an issue for a single-worker demo), which user/token it was built with, and its list of defined custom platforms (unchanged from before).

**The old "store a full base URL per device" model, and the ngrok-URL-changes-on-every-restart pain that came with it, are both gone.** The device needs no public inbound endpoint at all anymore — delete that requirement from your setup notes entirely, not just the registry schema.

### Still open: "what's this device's full current state" (reframed, not solved)
`hello`'s connection-registry presence answers "is this device online right now" for free. It does **not**, by itself, answer "what's its full active-provider list" for populating checkboxes on page load — the hash only tells you whether it *matches* what you last pushed, not what it actually contains. Same two options as before:
- Simplest: keep trusting the portal's own last-pushed state as the source of truth (now cross-checked against the `hello` hash for drift detection, which is new).
- More correct: a `status` message type the agent could send on request — not built; flag back if the demo needs it.

---

## Page 2: Activity Logs

### What it does
Shows every captured event the portal has received from all devices, newest first.

### Data source
Populated by whatever the portal's ingest endpoint receives — remember, per the tech-stack note above, this is the **companion FastAPI process**, not Streamlit itself. The agent POSTs there periodically with this envelope:
```json
{
  "records": [ /* array of event objects, shapes below */ ],
  "source_type": "browser_ai_prompt"
}
```
A single batch can contain a mix of the two record shapes below — check which fields are present to tell them apart.

### Record shape A: a captured conversation turn (prompt + AI reply)
```json
{
  "id": "uuid",
  "captured_at": "2026-07-30T08:34:59Z",
  "provider": "chatgpt",
  "provider_display_name": "ChatGPT",
  "page_title": "ChatGPT - New chat",
  "url": "https://chatgpt.com/c/...",
  "prompt": "the user's typed message",
  "response": "the AI's reply",
  "prompt_length": 42,
  "response_length": 210,
  "attachments": [
    {
      "mime_type": "image/png",
      "filename": "pasted_image.png",
      "source": "clipboard",
      "size_bytes": 48213,
      "sha256": "...",
      "data_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    }
  ],
  "capture_details": {"settle_reason": "response_settled", "has_response": true, "has_attachments": true}
}
```
`provider` is one of the six keys above, or the real name of any `custom_apps`-added platform (e.g. `grok`, `perplexity`) derived from its matched domain/title. Falls back to `custom_ai` only if no usable name could be derived.

**`attachments` is new** — images the user pasted (clipboard) or attached via the native file-picker dialog, alongside this turn. Always present as a field; an empty array `[]` for an ordinary text-only turn. `source` is either `"clipboard"` or `"file_dialog"`. `data_base64` is the raw image bytes, base64-encoded — decode and render as an `<img>`/`st.image` from that directly, no separate fetch needed. **A turn can have `prompt == ""` with a non-empty `attachments` list** — that's a real, valid case: the user sent an image with no caption at all. Don't treat an empty prompt as "nothing to show" for these rows. **Not captured**: drag-and-drop attachments — see the agent repo's `docs/KT_GUIDE.md` for why (investigated, not a simple gap).

### Record shape B: a security/detection alert (no AI reply involved)
```json
{
  "event_id": "uuid",
  "timestamp": "2026-07-30T08:34:59Z",
  "source_app": "chrome.exe",
  "window_title": "ChatGPT - New chat",
  "detection_type": "jailbreak_attempt",
  "confidence": 0.9,
  "rule_matched": "instruction_override",
  "raw_snippet": "the flagged text, truncated to 300 chars"
}
```
**Known gap**: this shape does not carry a clean `provider` field — only `source_app` (a raw process name). Possible follow-up if a provider tag is wanted here too.

### UI requirements
- **Tag per row**: use `provider_display_name` (shape A) as a colored badge/chip. For shape B rows, label with `source_app` until/unless the gap above is closed.
- **Timestamp**: absolute time plus a relative "X minutes/hours ago", recomputed client-side on an interval — in Streamlit, this means either `streamlit-autorefresh` or a manual refresh button, since the page won't update on its own once rendered.
- **Sort order**: newest first, default and only order.
- **Row content**: prompt/response preview (truncate long text, "show more" via `st.expander`), device/source app, tag + relative time.
- **Attachments**: if `attachments` is non-empty, render each one as a thumbnail (`st.image(base64.b64decode(att["data_base64"]))`) inside the row/expander, with a small caption showing `filename` and `source`. Handle the image-only case explicitly in the layout — an empty `prompt` next to a rendered thumbnail is the normal, expected look for that row, not a missing-data bug.
- Pagination or infinite scroll once volume grows — `st.dataframe`/`st.data_editor` handle basic paging reasonably; revisit if volume outgrows that. Note `data_base64` blobs make rows meaningfully heavier than a text-only feed — if using `st.dataframe` for the main table, keep attachments out of that table and render them only in the per-row expander, rather than loading every image up front.

---

## End-to-end test flow (how this actually gets exercised)

Only **one** tunnel is needed now — exposing the portal. The test device needs no public inbound endpoint at all anymore (that's the entire point of the WebSocket migration): it dials out to the portal for both directions, same as any normal outbound HTTPS/WSS client.

1. **Tunnel (exposes the portal)** — start the portal (the Streamlit UI, the companion ingest process, and the companion WS route all need to be reachable; either tunnel all the relevant ports, or put a reverse proxy in front locally and tunnel just that) on your machine, run `ngrok http <port>`. Note the public URL — a test device's `backend_url` and `ws_url` both need to point at this (the ingest/WS process specifically, not the Streamlit UI port; `ws_url` uses `wss://`, not `https://`).
2. **Log in & get a token** — log into the portal (Page 0), copy the issued token.
3. **Build the exe** — set `backend_url` to `<tunnel URL>/api/ingest/activity`, `ws_url` to `wss://<tunnel host>/ws/agent`, `auth_header_value` to `Bearer <token>`, package the exe.
4. **Run it on the test device** — no tunnel, no port-forwarding, no registration step needed on the device side at all. It connects out on its own.
5. **Confirm the device shows up as connected** — the portal should see a `hello` message and register the device (by `device_id`) as online.
6. **Confirm the outbound (ingest) direction works** — use an allowlisted app on the test device, confirm the record shows up in Activity Logs.
7. **Confirm the inbound (config-push) direction works** — on Provider Management, change what's active, including adding a `custom_apps` entry — save, confirm the already-running exe picks it up live (check `agent.log` for `publish` received / `publish_result` sent).
8. **Confirm domain matching** — push a `custom_apps` entry with `domain_pattern` (e.g. `grok.com`) for a site not in the fixed six, visit it on the test device, and confirm: (a) the event is captured and labeled with the correct domain-derived provider name, (b) the prompt is present, (c) the response is also present now (response-extraction is fixed) — flag it back only if response is empty even for the original six providers, which would indicate a real regression.
9. **Confirm attachments** — on the test device, paste an image into a monitored chat with a caption, send, and confirm the record's `attachments` array has one entry that renders correctly; then send an image with no caption at all, and confirm that record too (empty `prompt`, non-empty `attachments`) — this is the case most likely to get skipped by an implementation that assumes every row has real prompt text. Drag-and-drop is expected to NOT show up as an attachment — that's correct, not a bug to chase.
10. **Confirm reconnection** — kill the test device's network briefly (or restart the agent), confirm it reconnects with visible backoff in `agent.log`, and that a `publish` sent while it was disconnected either gets picked up via the `hello`-hash drift check on reconnect, or is at least clearly visible as "device was offline" in the portal rather than silently lost.

---

## Summary of what needs building
1. **Streamlit app** with Page 0 (login), Page 1 (Provider Management), Page 2 (Activity Logs), using `st.session_state` and the multi-page `pages/` convention.
2. **A small companion FastAPI process** (run via `uvicorn`) exposing (a) the ingest POST route, writing to a store (CSV/SQLite) that the Streamlit Activity Logs page reads from, and (b) the `wss://.../ws/agent` route that devices connect to for config-push (see Page 1) — this is the piece that doesn't fit inside Streamlit itself, decide this early; it now does more than just ingest.
3. Login: one (or a few) demo users in a CSV, email/password check, issues a token on success.
4. A **connection registry** keyed by `device_id` (which user/token it's using + a list of defined custom platforms per device + whether it's currently connected) — CSV/JSON-blob is fine. No base URL field needed anymore — devices aren't reached by URL.
5. Provider Management page: 6 checkboxes per device, plus one checkbox per defined custom platform, plus an "add custom platform" form offering a domain input for browser targets and a title-keyword input for IDE/desktop targets. Full-replace `publish` message sent down that device's live WS connection on Save.
6. Activity Logs page: table/feed reading from the ingest store, tagged and sorted per the rules above, with an autorefresh or manual-refresh mechanism, rendering `attachments` thumbnails per row (including the image-only, empty-prompt case).
7. Flag back to the agent side if a device `status` message type is wanted, and/or if detection-alert rows (shape B) need a real provider tag.
8. Storage note for `attachments`: base64 image blobs are meaningfully bigger than anything this store has held so far (up to ~11MB per image after base64 inflation of the agent's 8MB cap) — if using a CSV for the ingest store, this will get unwieldy fast; SQLite (already suggested as an option) handles it fine. Decide before volume makes it a problem, not after.

---

## Future (not yet built) — Tier 2: unknown-platform discovery

Kept for reference only. **Do not build against this yet** — none of it exists on the agent side (no network watcher, no `NetworkFlagEvent`, no `record_type` field), and it was deliberately deferred as its own initiative (new native ETW dependency, likely elevated-privilege requirement, needs validation on a real device before any of this is real).

The idea, if/when it's built: a third page ("Review Queue") showing domains a monitored process connected to that aren't in the known list yet — metadata only (process, hostname, timestamp — never prompt/response content). An admin approves (promotes the domain into `custom_apps`/`providers` and pushes it to every registered device, not just the one that flagged it) or dismisses (suppressed going forward) each one. If this gets greenlit later, it'll come with its own updated spec for the ingest envelope (`record_type` field) and the exact promote/dismiss mechanics — don't guess ahead of that.
