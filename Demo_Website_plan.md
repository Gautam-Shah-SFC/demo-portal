# Demo Portal — Website Plan

This is a handoff spec for building the Demo Portal website that talks to the endpoint agent (this repo). It covers the two pages that reflect what's **actually implemented today**: **Provider Management** (add/remove which browsers and IDEs a device monitors) and **Activity Logs** (view what's been captured).

**Scope note (read this first)**: an earlier draft of this doc described a third page ("Review Queue") and a `network_flag`/`record_type` mechanism for detecting *unknown* AI platforms via network-level telemetry. That's the "Tier 2" idea from `modification_needed.md` — it was discussed and deliberately **deferred as a separate, larger initiative** (new native dependency, likely elevated-privilege requirement, not yet built). The agent has no network watcher, no `NetworkFlagEvent`, and never sends a `record_type` field. Don't build Page 3 or anything referencing it yet — see "Future (not yet built)" at the bottom for that design, kept for reference only.

## Tech stack for this build: Streamlit (for now)

Use **Streamlit** (Python) to build this. A few things worth planning around given Streamlit's specific model, so this doesn't turn into a surprise partway through:

- **Streamlit is a reactive dashboard framework, not a general web server** — the whole script re-runs top-to-bottom on every user interaction. Use `st.session_state` for anything that needs to persist across reruns (login state, which device is selected, etc.), and Streamlit's built-in multi-page support (a `pages/` directory, one file per page — `pages/1_Provider_Management.py`, `pages/2_Activity_Logs.py`, etc.) for the Page 0/1/2 structure this doc describes.
- **⚠️ The ingest endpoint (agent → portal) does NOT fit naturally into Streamlit.** Streamlit has no built-in way to expose a plain `POST /api/ingest/activity` route that accepts arbitrary JSON from an external HTTP client (the agent's `uploader.py` doesn't know or care about Streamlit — it just POSTs JSON to whatever URL is in `backend_url`). This needs to be handled as a **separate, small companion process**:
  - Run a minimal **FastAPI** app (a few lines — one route, write incoming records to a shared SQLite file or CSV) alongside Streamlit, on a different port (`uvicorn`), and point the agent's `backend_url` at *that* process, not at Streamlit itself.
  - The Streamlit app's Activity Logs page then just reads from that same shared store on each rerun (or on a timer — see below) — it never receives the POSTs directly.
  - This is the one piece of real architecture this demo needs beyond "just Streamlit," and it's worth deciding early rather than discovering it partway through building Page 2.
- **Auto-refreshing the Activity Logs page** (for the "keeps counting up" relative-timestamp requirement, and for new events showing up without a manual reload) needs either the `streamlit-autorefresh` package or a manual "Refresh" button — Streamlit doesn't push updates to an open browser tab on its own.
- Suggested minimal dependency list: `streamlit`, `pandas` (for the logs table), `requests` (for calling the agent's webhook from the portal side), plus `fastapi` + `uvicorn` for the companion ingest listener.

## The two directions of communication

There are two completely separate API calls involved, going in opposite directions. Don't confuse them:

1. **Agent → Portal** (already working today): the agent ships captured events *to* the portal's ingest endpoint (the companion FastAPI process above). This is what feeds the **Logs page**.
2. **Portal → Agent** (built, needs a UI): the portal pushes provider config changes *to* the agent's webhook listener, running on the device itself. This is what the **Provider Management page** needs to call — this direction *is* a natural fit for Streamlit, since it's just an outbound `requests.post()` triggered by a button click in server-side Python.

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
   - the portal must send when calling *that device's* webhook (`/initialize`/`/publish`) — see the updated auth note in Page 1 below.

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
- **Adding a new custom platform** creates a new entry in this list for that device, `enabled: true` by default, and immediately fires a `/publish` including it.
- **Every subsequent visit to the page** renders one checkbox per entry in this list, in addition to the 6 fixed ones.
- **Toggling any checkbox and hitting Save** recomputes the full `providers` list and the full `custom_apps` list (pulled from each *checked* custom entry's stored definition) and sends both in one `/publish` call — no new agent-side capability needed, purely how the portal builds the payload from its own remembered state.
- Unchecking a custom platform just means the next `/publish` omits it — but keep its definition in the registry so it can be re-checked later without redefining it.

### UI
- One row/card per device (see "Device registry" below), showing 6 checkboxes/toggles for the known providers, plus one checkbox per custom platform already defined for that device, plus an "add custom platform/IDE" form to define a new one.
- A **Save** (or **Apply**) button per device that sends the *complete* checked set in one call — both `providers` and `custom_apps`.
- Show the response as a success toast (`active_providers` + `active_custom_apps` echoed back) or an error banner (see error cases below).

### ⚠️ Critical semantic: full replace, not add/remove
Despite the page being framed as "add or remove," the underlying API call is **not incremental**. Every call must include the **entire desired set** of active providers, not just the one being toggled. If the device currently has `["chatgpt", "cursor"]` active and the admin unchecks nothing but adds Gemini, the call must send `["chatgpt", "cursor", "gemini"]` — sending just `["gemini"]` would turn ChatGPT and Cursor **off**. The frontend must always compute and send the full checkbox state, never a delta.

### API call to make (from the portal's backend, not the browser directly)
```
POST <device base URL from the registry>/publish
Authorization: <auth_header_name>: <auth_header_value>
Content-Type: application/json

{
  "providers": ["chatgpt", "gemini", "cursor"],
  "custom_apps": [
    {"process_name": "chrome.exe", "domain_pattern": "grok\\.com"}
  ]
}
```
- Use `/initialize` instead of `/publish` for a device's first-ever config push; both do the exact same thing server-side.
- Default port is `8765` on the device itself (configurable via `agent_config.yaml`'s `webhook_port`) — irrelevant when reaching a device through an ngrok tunnel, since the tunnel's own public URL is what gets called instead.
- `auth_header_name`/`auth_header_value` are whatever's configured in that device's `agent_config.yaml` — the same token that user got at login (Page 0) and that's baked into that specific device's config. Per-user/per-device, so the portal needs to look up the right token for the device it's calling.

### Responses to handle
| Status | Meaning | Body |
|--------|---------|------|
| 200 | Applied successfully | `{"status": "ok", "active_providers": [...], "active_custom_apps": [...]}` |
| 400 | Bad payload — missing/wrong-typed `providers`; a `custom_apps` entry missing/bad `process_name`; for a browser `process_name`, neither `domain_pattern` nor `window_title_pattern` provided, or `domain_pattern` isn't a valid regex; for a non-browser `process_name`, invalid `window_title_pattern` | `{"status": "error", "message": "..."}` (message identifies which entry/field) |
| 401 | Auth header missing or wrong | `{"status": "error", "message": "unauthorized"}` |
| 404 | Wrong path | `{"status": "error", "message": "not found"}` |
| 500 | Device failed to persist the change | `{"status": "error", "message": "..."}` |
| (no response / timeout) | Device unreachable — wrong host/port, firewall, or agent not running | handle as a network error |

### Device registry (needed, doesn't exist yet)
The agent has no self-registration or discovery mechanism. The portal needs its own simple table of known devices: a name/label, a base URL to reach it at, which user/token that device was built with, and its list of defined custom platforms — entered manually by whoever sets up a device. A CSV (or one small JSON blob per device) is fine for this too.

**Store a full base URL, not separate host+port fields.** For the ngrok-tunnel test topology, the portal only ever sees the tunnel's public URL (e.g. `https://abcd1234.ngrok-free.app`), not the device's own `webhook_port` (8765) directly. A free ngrok tunnel gets a **new random URL every time it's restarted** — whoever restarts the tunnel needs to update the registry entry each time.

### Also needed on the agent side, not built yet (flagging, not blocking)
There is currently **no way to ask a device "what's currently active"** — the webhook is push/write-only. If the checkboxes need to reflect true current state on page load, one of two things is needed:
- Simplest: the portal treats its own last-pushed state as the source of truth and remembers it in its own store.
- More correct: ask for a `GET /status` endpoint to be added to the agent's webhook listener. Flag this back if the demo needs it.

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
  "capture_details": {"settle_reason": "response_settled", "has_response": true}
}
```
`provider` is one of the six keys above, or the real name of any `custom_apps`-added platform (e.g. `grok`, `perplexity`) derived from its matched domain/title. Falls back to `custom_ai` only if no usable name could be derived.

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
- Pagination or infinite scroll once volume grows — `st.dataframe`/`st.data_editor` handle basic paging reasonably; revisit if volume outgrows that.

---

## End-to-end test flow (how this actually gets exercised)

Two separate ngrok tunnels are involved, one per direction — don't conflate them:

1. **Tunnel 1 (exposes the portal)** — start the portal (both the Streamlit UI and the companion ingest process need to be reachable; either tunnel both ports, or put a reverse proxy in front locally and tunnel just that) on your machine, run `ngrok http <port>`. Note the public URL — this is what a test device's `backend_url` needs to point to (pointed at the ingest process specifically, not the Streamlit UI port).
2. **Log in & get a token** — log into the portal (Page 0), copy the issued token.
3. **Build the exe** — set `backend_url` to Tunnel 1's URL + ingest path, `auth_header_value` to `Bearer <token>`, package the exe.
4. **Tunnel 2 (exposes the test device)** — copy the exe to the test device, run it, then run `ngrok http 8765` on the test device itself. Independent from Tunnel 1.
5. **Register the device** — add a new device entry in the portal, set its URL to Tunnel 2's URL.
6. **Confirm the outbound direction works** — use an allowlisted app on the test device, confirm the record shows up in Activity Logs.
7. **Confirm the inbound direction works** — on Provider Management, change what's active, including adding a `custom_apps` entry — save, confirm the already-running exe picks it up live.
8. **Confirm domain matching** — push a `custom_apps` entry with `domain_pattern` (e.g. `grok.com`) for a site not in the fixed six, visit it on the test device, and confirm: (a) the event is captured and labeled with the correct domain-derived provider name, (b) the prompt is present, (c) the response is also present now (response-extraction is fixed) — flag it back only if response is empty even for the original six providers, which would indicate a real regression.

---

## Summary of what needs building
1. **Streamlit app** with Page 0 (login), Page 1 (Provider Management), Page 2 (Activity Logs), using `st.session_state` and the multi-page `pages/` convention.
2. **A small companion FastAPI process** (run via `uvicorn`) for the ingest endpoint, writing to a store (CSV/SQLite) that the Streamlit Activity Logs page reads from — this is the one piece that doesn't fit inside Streamlit itself, decide this early.
3. Login: one (or a few) demo users in a CSV, email/password check, issues a token on success.
4. A device registry (name + base URL + which user/token it's using + a list of defined custom platforms per device) — CSV/JSON-blob is fine.
5. Provider Management page: 6 checkboxes per device, plus one checkbox per defined custom platform, plus an "add custom platform" form offering a domain input for browser targets and a title-keyword input for IDE/desktop targets. Full-replace POST to `/publish` on Save.
6. Activity Logs page: table/feed reading from the ingest store, tagged and sorted per the rules above, with an autorefresh or manual-refresh mechanism.
7. Flag back to the agent side if a `GET /status` endpoint is wanted, and/or if detection-alert rows (shape B) need a real provider tag.

---

## Future (not yet built) — Tier 2: unknown-platform discovery

Kept for reference only. **Do not build against this yet** — none of it exists on the agent side (no network watcher, no `NetworkFlagEvent`, no `record_type` field), and it was deliberately deferred as its own initiative (new native ETW dependency, likely elevated-privilege requirement, needs validation on a real device before any of this is real).

The idea, if/when it's built: a third page ("Review Queue") showing domains a monitored process connected to that aren't in the known list yet — metadata only (process, hostname, timestamp — never prompt/response content). An admin approves (promotes the domain into `custom_apps`/`providers` and pushes it to every registered device, not just the one that flagged it) or dismisses (suppressed going forward) each one. If this gets greenlit later, it'll come with its own updated spec for the ingest envelope (`record_type` field) and the exact promote/dismiss mechanics — don't guess ahead of that.
