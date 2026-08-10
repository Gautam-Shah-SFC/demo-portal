# ServiceNow AI Usage Connector — Design Doc

**Status: built and verified against a real ServiceNow dev instance** (`dev414198.service-now.com`). This revision replaces the original pre-implementation draft — several of its assumptions (table names, auth flow, storage model) turned out wrong or over-scoped once tested against a real instance; see §8 for exactly what changed and why. For the narrative version of how those findings were reached (dead ends included), see `KT_Guide.md`.

## Project Meta
- **Parent project:** Demo Portal (backend for the AI Endpoint Monitoring Agent)
- **Goal:** Extend the Demo Portal backend to ingest prompt/response logs from ServiceNow's AI features (Now Assist, Virtual Agent, custom Agentic AI agents) and normalize them into the same logs pipeline/dashboard already used for the Windows endpoint agent.
- **Approach taken:** A `servicenow/` connector package inside the existing Demo Portal backend (not a separate service, not browser scraping) — a background poll loop (default) plus an optional signed webhook route, both feeding the same SQLite `events` store the endpoint agent already writes to.
- **Scale**: built to fit this portal's actual architecture — single-tenant demo (Streamlit + one FastAPI companion process, JSON-file + SQLite storage) — not the multi-tenant/KMS-backed production system implied by the original draft. See §1 and §8 for the specific simplifications and why they're safe for this project.
- **Relationship to existing system:** ServiceNow rows land in the exact same `events` table as agent rows, tagged `provider: "servicenow"` — Activity Logs required no schema change to display them, just a source filter (§6).

---

## 1. Data Model (as built)

**Scope constraint held**: only `prompt` and `response` are ever fetched, transmitted, or stored, plus the minimum needed for idempotency (`external_id`, scoped per connection — see below). No user identifiers, no model metadata, no raw row dumps.

### Connections — `data/servicenow_connections.json` (via `common/servicenow_store.py`)
A flat JSON list, not a DB table — this is a single-admin demo portal, so there's no concurrent-writer contention to justify a real database here. Each entry:
```
id                          -- uuid, generated on create
tenant_id                   -- always "default" (see §8 — no real multi-tenancy)
instance_url
auth_type                   -- "basic" | "oauth2"
username
password_encrypted          -- Fernet, see §2
client_id                   -- oauth2 only
client_secret_encrypted     -- oauth2 only
tables_enabled               -- e.g. ["sys_cs_message"]
sync_mode                   -- "poll" | "webhook"
poll_interval_sec
webhook_secret_encrypted    -- webhook only, admin-generated on create
last_sync_watermark         -- {table: max sys_created_on seen}
status                      -- "active" | "error" | "paused"
last_error
last_synced_at
```
Credentials are Fernet-encrypted at rest (§2). There is currently **no "edit connection" UI** — only create/pause/resume/delete. Changing `tables_enabled` means deleting and recreating the connection. Worth adding if this sees real use.

### Logs — same `events` table the agent already writes to (`common/store.py`)
Extended **additively**, so the agent's ingest path (`insert_records()`) is untouched:
```sql
ALTER TABLE events ADD COLUMN source_type TEXT;     -- NULL for every existing/agent row
ALTER TABLE events ADD COLUMN external_id TEXT;      -- NULL for every existing/agent row
CREATE UNIQUE INDEX idx_events_source_external
  ON events(source_type, external_id) WHERE external_id IS NOT NULL;
```
A new `upsert_record(record, source_type, external_id)` function (used only by the ServiceNow path) does an `INSERT ... ON CONFLICT(source_type, external_id) WHERE external_id IS NOT NULL DO UPDATE ...` — SQLite requires that `WHERE` clause to be repeated on the `ON CONFLICT` target itself to match a partial unique index; omitting it fails with "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint" (see KT_Guide.md for how this was found).

`external_id` is **`f"{connection_id}:{sys_id}"`**, not bare `sys_id` — `sys_id` is only unique *within* one ServiceNow instance, so two different connections (two different customer instances) could otherwise collide and silently overwrite each other's rows.

Each ServiceNow-sourced row in `events.raw_json`:
```json
{
  "id": "<sys_id>",
  "provider": "servicenow",
  "provider_display_name": "ServiceNow",
  "source_subtype": "now_assist" | "virtual_agent" | "custom_agent",
  "prompt": "...",
  "response": "...",
  "captured_at": "..."
}
```
No `tenant_id` is stored per-row — not needed without real multi-tenancy (§8).

---

## 2. Auth Handling (as built)

- **OAuth2 = Resource Owner Password Credentials grant** (`grant_type=password` against `/oauth_token.do`), not Authorization Code. Deliberate: Authorization Code needs a publicly reachable HTTPS redirect URI, which this portal doesn't reliably have (ngrok tunnel, changes on restart). Password grant needs no redirect and still yields a real access/refresh token pair. `servicenow/auth_client.py` caches the token per connection in memory and refreshes on expiry (falling back to a fresh password grant if the refresh token itself has gone stale).
- **Basic auth** is the simpler fallback and is what's actually been tested end-to-end against the real instance.
- Credentials encrypted at rest via **Fernet** (`common/crypto.py`), keyed off a locally-generated `data/fernet.key` (gitignored). This is not a KMS — acceptable for a single-admin demo, not for a real multi-tenant deployment (§8).
- ServiceNow-side write access is not needed for polling — only read access to the enabled table(s). The custom test table (§8) was created with the admin account only because nothing else existed to test against; a real customer connection only ever reads.

---

## 3. Sync Engine (as built)

### Poll (default)
Runs as a single **background `asyncio` task inside the existing `ingest_server` FastAPI process** (`servicenow/scheduler.py`, started on FastAPI startup) — not a separate worker process or per-tenant scheduler. Ticks every 5s; each connection is only actually synced once its own `poll_interval_sec` has elapsed since its last run. `sync_connection()` itself is blocking (`requests`), so it's dispatched via `asyncio.to_thread()` so it never stalls the WebSocket/ingest routes sharing this process.

```
for each active, poll-mode connection (checked every 5s, respecting its own poll_interval_sec):
  for each enabled table:
    watermark = last_sync_watermark[table] or epoch
    page via Table API: sysparm_query=sys_created_on>{watermark}^ORDERBYsys_created_on,
                         sysparm_limit=500, sysparm_offset incrementing
                         -- NOT sysparm_display_value=true, see §8 for why
    for each row:
      advance in-memory max_seen against EVERY fetched row's sys_created_on,
        even ones the mapper discards -- otherwise an all-noise page never
        advances the watermark and gets re-fetched forever
      mapped = mapper(row)  -- None if not real conversational content
      if mapped: upsert_record(..., external_id=f"{connection_id}:{row.sys_id}")
    persist watermark = max_seen, once per table, after the whole page loop
```
Retries with exponential backoff on network errors / 429 / 5xx (respects `Retry-After` if present), up to 3 attempts. Per-connection, per-table try/except — one table's failure doesn't stop another table or another connection; the connection's `status` flips to `error` with `last_error` set to a human-readable message, and back to `active` on the next fully-clean sync.

### Webhook (built, not verified end-to-end)
`POST /ingest/servicenow/{connection_id}` on the same `ingest_server` process, HMAC-SHA256-verified (`X-Signature` header, per-connection secret). Routes through the same `upsert_record()` path as polling. **Not restricted to localhost** — unlike `/internal/push/{device_id}`, this one genuinely needs to be reachable from the customer's ServiceNow instance.

The Connections page generates the webhook URL + secret and a Business Rule script template, but **the HMAC-signing line in that template is pseudocode** — ServiceNow doesn't have a trivial built-in HMAC-SHA256 helper in server-side Business Rule scripting, and nothing here has verified a working implementation against a real instance. Anyone wiring up webhook mode for real needs to solve that first. Poll mode has no such gap and is the recommended default.

---

## 4. Error Handling & Resilience (as built)
- Per-connection, per-table isolation — matches the original design intent.
- Exponential backoff on network failure / 429 / 5xx, `Retry-After` respected.
- Repeated failure → `status: "error"` + `last_error` shown in the Connections UI; polling continues to attempt on schedule (no automatic pause-after-N-failures — an admin has to notice and pause manually).
- **No dead-letter queue.** A row that fails mapping is simply skipped (mapper returns `None`) and never retried differently — there's no separate store or review UI for "rows that didn't map." Given `discover_table_schema()` lets an admin check real field names before enabling a table, this has been low-risk in practice, but it's a real gap versus the original design.

---

## 5. Schema Variance / Mapper Registry (as built, confirmed against a real instance)

`servicenow/mappers.py`:
```python
MAPPERS = {
    "sys_llm_log": map_now_assist_row,      # UNCONFIRMED -- no Now Assist license available to test against
    "sys_cs_message": map_virtual_agent_row, # CONFIRMED -- see below
    "u_ai_prompt_log": map_custom_ai_prompt_log_row,  # CONFIRMED -- demo/test table, see §8
}
```

### `sys_cs_message` (Virtual Agent / Conversational Interfaces) — confirmed real shape
This is **not** `cs_conversation` (the original draft's guess — that table never existed). The real table ships on every ServiceNow instance by default (base platform, no license needed), but its shape is nothing like a simple `{prompt, response}` row:

- **`sys_cs_conversation`** (the conversation record itself) holds **no prompt/response content at all** — only metadata (topic, state, title, a compressed context blob). Not pollable for content.
- **`sys_cs_message`** is where content actually lives, one row per message (not per turn):
  - `payload` is a **JSON-encoded string** describing a UI widget, not plain text.
  - The user's actual typed text is at `payload.searchText`, only on rows where `direction == "inbound"`.
  - The bot's actual reply text is at `payload.plainTextMessage` (fallback `payload.value`), only on rows where `direction == "outbound"` **and** `payload.uiType == "OutputText"`.
  - `is_bot_message` is **unreliable** — it reads `false` on every row observed, including the bot's own replies. Use `direction`, not this field.
  - Most rows are UI scaffolding (`TopicPickerControl` clicks, `ContextualAction` menus) with no real conversational content — the mapper returns `None` for these and they're silently skipped (not stored, not retried).
  - **Each row is only half a turn** — a user message OR a bot reply, never both. There is no pairing step; Activity Logs shows them as separate prompt-only and response-only rows tagged `source_subtype: "virtual_agent"`. True turn-pairing (grouping consecutive inbound/outbound rows by `conversation`) is not implemented — flagged as a real follow-up if clean paired turns are needed.
  - Direct **writes** to this table via the Table API are silently blocked by ServiceNow's own business-rule logic (every content field comes back blank on a raw `POST`, even though the HTTP status is `201`) — it can only be populated by ServiceNow's actual Virtual Agent conversation engine actually running (i.e., someone using the chat widget), not by an external system poking the table. Reads work fine once real data exists.

### `sys_llm_log` (Now Assist) — still unconfirmed
No Now Assist license was available on the test instance (`HTTP 400`, table doesn't exist), so `map_now_assist_row`'s field-name guesses (`prompt`/`input`/`user_input` → `response`/`output`/`generated_text`) remain untested. Run `discover_table_schema()` (exposed via the Connections page's "Test connection" button) against a real Now Assist-licensed instance before trusting it.

### `u_ai_prompt_log` (custom test table) — confirmed, used for end-to-end testing
Created via direct Table API calls (`POST sys_db_object` for the table, `POST sys_dictionary` for each field) rather than through Studio's UI — confirmed this works and provisions a normal, fully-functional custom table immediately, no separate publish step. Fields: `u_prompt`, `u_response` (plain strings, 4000 chars). This stands in for the design's third case — a customer's own custom Agentic AI agent logging to a table they already built — and is what was actually used to validate the whole pipeline end-to-end (auth → poll → mapper → upsert → Activity Logs) before real Virtual Agent data was available.

**On the "will a new customer need to create a table for us" question**: no. For a real customer already using Now Assist or Virtual Agent, `sys_llm_log`/`sys_cs_message` are already being populated by ServiceNow itself — onboarding is instance URL + read-only credentials, nothing more. `u_ai_prompt_log` was purely a stand-in for testing on a blank instance with neither AI feature licensed. The one real remaining gap is a **custom Agentic AI agent** logging to a table nobody but that customer knows the shape of — see §9 for the recommended fix (configurable field mapping instead of a hardcoded Python function per table).

---

## 6. Dashboard Integration (as built)
- No new table/schema needed for Activity Logs — ServiceNow rows already carry `provider`/`provider_display_name`, so they render through the exact same row-rendering code as agent rows.
- Added: a **"Filter by source"** multiselect (derived from whatever `provider_display` values are actually present, not hardcoded) and a `source_subtype` line in each conversation row's metadata.
- Added: a distinct badge color for `provider_key == "servicenow"` (`common/providers.py`'s `SOURCE_COLORS`), kept deliberately separate from the 6-entry `PROVIDERS` dict so it doesn't add a 7th checkbox to Provider Management (that page is about device-monitored browsers/IDEs, unrelated to this connector).
- **New "ServiceNow Connections" page** (`pages/3_ServiceNow_Connections.py`): add/pause/resume/delete a connection, "Test connection" (runs `discover_table_schema()` live against the real instance — this is what caught the `cs_conversation` vs `sys_cs_message` naming bug in practice), "Sync now" for an immediate manual pull, and webhook URL/secret/script display when in webhook mode.

---

## 7. Security & Data Minimization (as built)
- Holds: only `prompt`, `response`, `source_subtype`, and `external_id` (for dedup) ever leave a mapper function or get stored. Confirmed in `sys_cs_message`'s case — the mapper discards the entire rich `payload` JSON structure except the one text field it needs.
- Credentials encrypted at rest (Fernet, local key file — not a KMS, see §8).
- The webhook route (`/ingest/servicenow/{connection_id}`) is HMAC-verified; the internal device-push route it sits next to (`/internal/push/{device_id}`) is separately restricted to localhost-only requests — different routes, different threat models, not to be confused.
- Application logs (`agent.log`-equivalent / uvicorn output) never print `prompt`/`response` content — only status/counts, matching the original requirement.
- No field-level ACL enforcement beyond whatever the ServiceNow-side integration user's own role grants — this portal doesn't add its own row-level access control on top.

---

## 8. What Changed From the Original Draft, and Why

The original draft was written before any real ServiceNow instance was available to test against. Once one was (`dev414198.service-now.com`, a blank trial instance), several assumptions turned out wrong:

| Original assumption | Reality found | Fix |
|---|---|---|
| Table is `cs_conversation` | Never existed on any tested instance | Real table is `sys_cs_message`; `sys_cs_conversation` exists but holds no content |
| A connection's table is populated by simple rows with prompt/response fields | `sys_cs_message.payload` is JSON-encoded UI-widget data; each row is half a turn, not a full one | Mapper parses JSON, filters by `direction`+`uiType`, accepts the half-turn limitation |
| OAuth2 = standard Authorization Code flow | Needs a stable public HTTPS redirect URI this portal doesn't reliably have | Switched to Resource Owner Password Credentials grant (no redirect needed) |
| Multi-tenant `servicenow_connections` DB table, KMS-encrypted secrets, per-tenant staggered schedulers | This portal is a single-admin demo with one FastAPI process and no DB server | JSON file + Fernet-encrypted local key + one shared background asyncio loop |
| Dead-letter queue for unmapped rows | Not built | Unmapped rows are silently skipped; flagged as a real gap, not solved |
| `sysparm_display_value=true` for readability | Turns choice fields like `direction` into capitalized, locale-dependent display text ("Outbound") that silently broke every mapper comparison | Removed — raw internal values only |
| Watermark advances once per successful row | An all-filtered-out page (pure UI noise) never advanced the watermark, so it re-fetched the same noise every poll forever | Watermark now advances against every fetched row, mapped or not |

See `KT_Guide.md` for the investigation narrative behind each of these (what was tried, what failed, and how it was actually confirmed).

---

## 9. Recommended Next Steps
1. **Configurable field mapping**, not hardcoded Python mappers. Today, supporting a brand-new customer's custom Agentic AI agent table requires a code change (a new function in `servicenow/mappers.py`). For real onboarding to be self-service, the Connections form should let an admin type a table name + which field holds the prompt + which field holds the response, with `sys_llm_log`/`sys_cs_message` pre-filled as smart (but still editable) defaults. This was explicitly raised mid-build and deferred — worth doing before onboarding a real second customer.
2. **Turn-pairing for `sys_cs_message`** — group consecutive inbound/outbound rows by `conversation` into real `{prompt, response}` pairs instead of separate half-turn rows, if clean paired turns matter more than getting something working quickly.
3. **Verify `sys_llm_log` mapper** against a real Now Assist-licensed instance — currently field-name guesses only.
4. **Verify the webhook path end-to-end**, including a real working HMAC-SHA256 Business Rule script — currently unverified pseudocode.
5. **"Edit connection" UI** — currently delete + recreate is the only way to change `tables_enabled`.
6. Real multi-tenancy / KMS if this ever needs to serve more than one admin/org.
