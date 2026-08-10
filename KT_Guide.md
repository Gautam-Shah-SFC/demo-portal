# ServiceNow AI Usage Connector — KT Guide

This is the narrative companion to `servicenow_Design.md`. That doc describes what was built and its final shape; this one walks through **how it was actually gotten right** — the wrong turns, the real gotchas found by testing against a live ServiceNow dev instance, and what to do (and not repeat) if you're picking this up next.

If you just want to use the connector, skip to **"How to add and test a connection"**. If something's broken or you're extending it, the **"Gotchas"** section below is where the non-obvious failure modes live.

---

## What this is, in one paragraph

The Demo Portal already had one ingestion path: a Windows endpoint agent posting captured browser/IDE AI prompts to `/api/ingest/activity`. This connector adds a second, independent path — ServiceNow's own AI features (Now Assist, Virtual Agent, or a customer's custom agent) — that lands in the *same* `events` table and the *same* Activity Logs page, tagged `provider: "servicenow"`. It runs as a background poll loop inside the existing FastAPI companion process (`ingest_server`), with an optional signed webhook as a lower-latency alternative.

---

## Where the code lives

```
common/crypto.py              Fernet encrypt/decrypt for credentials at rest
common/servicenow_store.py    JSON-file connection registry (data/servicenow_connections.json)
common/store.py               +upsert_record() -- idempotent insert-or-update, additive to the existing schema
servicenow/auth_client.py     OAuth2 (password grant) / Basic auth, token caching+refresh
servicenow/http.py            auth-aware GET wrapper
servicenow/mappers.py         per-table {prompt, response} extraction + schema-discovery probe
servicenow/sync.py            the actual poll loop: paginate, map, upsert, advance watermark
servicenow/scheduler.py       background asyncio task, ticks every 5s
ingest_server/main.py         starts the scheduler on FastAPI startup; POST /ingest/servicenow/{id} webhook route
pages/3_ServiceNow_Connections.py   admin UI
pages/2_Activity_Logs.py      +source filter, +source_subtype display (small addition)
common/providers.py           +SOURCE_COLORS, so ServiceNow gets a badge without becoming a 7th provider checkbox
```

---

## How to add and test a connection

1. Get an instance URL + credentials. **Poll mode with Basic auth is the easiest first test** — it needs nothing on the ServiceNow side beyond a user with read access to whatever table you're pointing at.
2. Open **ServiceNow Connections** in the portal → **"Add a new connection"**.
3. Fill in instance URL, auth, and pick a table:
   - `sys_llm_log` — Now Assist. Only exists if that plugin/license is active. **Unconfirmed field mapping** — see Gotchas.
   - `sys_cs_message` — Virtual Agent / Conversational Interfaces. Exists on every instance by default. **Confirmed, but only half-turns** — see Gotchas.
   - `u_ai_prompt_log` — a plain custom table (see below) for exercising the pipeline without needing either AI feature licensed.
4. Click **"Test connection"** — this runs `discover_table_schema()` for real, against the real instance, before you commit to syncing. If it 400s, the table doesn't exist on this instance; don't enable it.
5. Click **"Resume"** (connections are created `paused` on purpose, so nothing syncs before you've tested it).
6. Click **"Sync now"** for an immediate pull, or just wait for the poll interval.
7. Check **Activity Logs**, filter by source = "ServiceNow".

### If you don't have `sys_llm_log` or `sys_cs_message` populated with anything

**`sys_llm_log`**: needs Now Assist licensed. Not something you can fake your way into on a trial instance.

**`sys_cs_message`**: exists by default but starts empty. To generate real data:
- Log into the instance's standard UI.
- Search the Application Navigator for `Virtual Agent` → open **Designer**.
- Click **"Test active topics"** (top-right) — this opens a real chat panel using whatever topics are already published (stock instances ship with several demo topics: Greetings, FAQ Conversation Builder, etc.).
- Send a couple of messages. This generates a real `sys_cs_conversation` + several `sys_cs_message` rows.
- **Do not try to `POST` fake rows directly into `sys_cs_message` via the Table API** — it silently doesn't work. See Gotchas.

### Creating a custom test table (`u_ai_prompt_log`) via API, not Studio

If neither AI feature is licensed on your test instance, you can still exercise the whole pipeline with a plain custom table, created without touching Studio's UI:

```bash
# 1. Create the table
curl -X POST "https://<instance>/api/now/table/sys_db_object" -u user:pass \
  -H "Content-Type: application/json" \
  -d '{"name":"u_ai_prompt_log","label":"AI Prompt Log"}'

# 2. Add fields
curl -X POST "https://<instance>/api/now/table/sys_dictionary" -u user:pass \
  -H "Content-Type: application/json" \
  -d '{"name":"u_ai_prompt_log","element":"u_prompt","column_label":"Prompt","internal_type":"string","max_length":"4000","active":"true"}'
curl -X POST "https://<instance>/api/now/table/sys_dictionary" -u user:pass \
  -H "Content-Type: application/json" \
  -d '{"name":"u_ai_prompt_log","element":"u_response","column_label":"Response","internal_type":"string","max_length":"4000","active":"true"}'

# 3. Insert / read to confirm it actually works
curl -X POST "https://<instance>/api/now/table/u_ai_prompt_log" -u user:pass \
  -H "Content-Type: application/json" \
  -d '{"u_prompt":"hello","u_response":"hi"}'
```
This genuinely works and provisions a fully functional table immediately — `sys_db_object` insert triggers ServiceNow's own table-creation engine (the same thing Studio's "Create Table" wizard does under the hood), no separate publish step. Confirmed with a real insert-then-read round trip.

**This is a testing convenience only.** A real customer already using Now Assist or Virtual Agent never does this — those tables already exist and are already being populated by ServiceNow itself. Don't read this section as "customers need to create a table for us."

---

## Gotchas (in the order they were actually hit)

### 1. `sys_plugins` is ACL-restricted, even for admin, via the Table API
Trying to introspect which AI plugins are active by querying `sys_plugins` directly returns `"User Not Authorized" / "Failed API level ACL Validation"` — even as admin. Don't rely on this table for plugin discovery. Instead, just try the candidate table name directly (`GET /api/now/table/<name>?sysparm_limit=1`): `400` means it doesn't exist, `200` means it does (empty or not).

### 2. `cs_conversation` (the original design's guess) never existed
It was a plausible-sounding guess, not a real ServiceNow table name. Confirmed via direct probe (`400` on every instance tested). The real table is `sys_cs_conversation` (conversation metadata only) and `sys_cs_message` (actual content, one row per message).

### 3. `sys_cs_conversation` exists by default but has no prompt/response fields at all
Checked its `sys_dictionary` entries directly — it's topic/state/title/context metadata, no plain-text content field anywhere. Not useful to poll for content. This is why the mapper targets `sys_cs_message` instead.

### 4. Direct writes to `sys_cs_message` / `sys_cs_conversation` don't work, even though the HTTP status says they did
```
POST /api/now/table/sys_cs_message  {"conversation": "...", "payload": "hello", "direction": "inbound", ...}
→ 201 Created
→ but the response body shows payload="", direction="", conversation="" -- everything blanked
```
ServiceNow has business-rule logic guarding these tables that silently strips a raw external `POST`. They're only meant to be populated by the real Virtual Agent conversation engine actually running a conversation (the chat widget), not by an integration writing to them directly. **A 201 status does not mean the write did what you asked** — always read back what you wrote before trusting it, especially on system tables you didn't design.

This is a genuinely different category of table from `u_ai_prompt_log` (a plain custom table, which behaves like a normal dumb table with no such interception) — don't assume all ServiceNow tables behave the same way under direct API writes.

### 5. `sys_cs_message.payload` is JSON-encoded UI-widget data, not plain text
A raw `payload` value looks like:
```json
{"uiType":"TopicPickerControl","plainTextMessage":"What's your issue...","searchText":"hwy just want to talk with you", ...}
```
The user's real typed text is at `.searchText` (only meaningful on `direction: "inbound"` rows — most inbound rows have this empty and are just UI clicks). The bot's real reply text is at `.plainTextMessage` (fallback `.value`), only on `direction: "outbound"` rows where `.uiType == "OutputText"` (other outbound uiTypes are menus/pickers, not real replies). This was worked out by actually generating a real conversation (via the chat widget) and reading the raw rows back — not by guessing from field names alone, which would have been wrong (`is_bot_message`, the obviously-named field for exactly this, is unreliable — see next).

### 6. `is_bot_message` reads `false` on every row, including the bot's own messages
Don't use it. Use `direction` (`"inbound"` = user → bot, `"outbound"` = bot → user) instead. Found by inspecting real rows side-by-side with what was actually typed/received in the chat widget.

### 7. `sysparm_display_value=true` silently broke the whole mapper
The sync engine originally requested display values "for readability" while building it. This turned `direction` into `"Outbound"` / `"Inbound"` (capitalized display text) instead of the raw `"outbound"` / `"inbound"` internal value the mapper compares against — a case-sensitive `==` check, so it just silently matched nothing, for every row, forever. No error, no exception — `sync_connection()` reported `status: "active"`, `last_error: None`, and simply synced zero rows. This is the kind of bug that only shows up by checking actual output counts, not by watching for exceptions. Fixed by dropping `sysparm_display_value` entirely — raw internal values are also more correct for programmatic mapping anyway (display text is locale-dependent; internal values aren't).

### 8. Watermark only advancing past *mapped* rows means an all-noise page re-fetches forever
Once (7) was fixed and rows started mapping successfully, a related bug surfaced: the watermark-tracking logic only updated `max_seen` for rows that passed the mapper. A page of *only* filtered-out UI-scaffolding rows (common — `sys_cs_message` has a lot of these) would never advance the watermark, so the next poll would re-fetch the exact same noise page again, indefinitely. Not data-incorrect (upsert dedupes), just wasteful and worth catching. Fixed by tracking `max_seen` against every fetched row's `sys_created_on`, before the mapper even runs.

### 9. SQLite's partial-index `ON CONFLICT` needs the `WHERE` clause repeated on the conflict target
```sql
-- Fails: "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"
INSERT INTO events (...) VALUES (...)
ON CONFLICT(source_type, external_id) DO UPDATE SET ...

-- Works:
INSERT INTO events (...) VALUES (...)
ON CONFLICT(source_type, external_id) WHERE external_id IS NOT NULL DO UPDATE SET ...
```
The unique index (`idx_events_source_external`) is partial (`WHERE external_id IS NOT NULL`, so agent-path rows with a NULL `external_id` never collide with each other). SQLite requires an `ON CONFLICT` upsert targeting a partial index to repeat that same `WHERE` clause verbatim on the conflict-target itself, or it won't recognize the index as a valid target at all — this isn't documented anywhere obvious and just fails with a generic-sounding constraint error. Confirmed by reproducing it directly against the real `events` table before finding the fix.

### 10. `sys_id` alone isn't a safe dedup key across connections
Initially `external_id` was just the bare `sys_id`. Two different connections (two different ServiceNow instances/customers) could in principle produce the same `sys_id` and silently overwrite each other's stored rows, since `sys_id` is only guaranteed unique *within* one instance. Fixed by scoping to `f"{connection_id}:{sys_id}"`. Verified with a test that deliberately pointed two separate connections at the same fake rows and confirmed they landed as 6 distinct rows, not 3.

### 11. Test buttons can be misleading if you leave stale table selections checked
`sync_connection()` marks a connection's overall `status` as `"error"` if *any* enabled table fails — even if another enabled table on the same connection is syncing real data successfully. If you multi-select `sys_llm_log` + `sys_cs_message` + `u_ai_prompt_log` and only the last one is real, the connection will show red/error forever despite working data flowing. Only enable tables that actually exist on the target instance.

---

## Known limitations (not bugs, just not done)

- No "edit connection" — delete and recreate to change `tables_enabled`.
- `sys_cs_message` rows land as separate prompt-only / response-only entries, not paired turns (no conversation-grouping pass implemented).
- `sys_llm_log`'s mapper field names are unconfirmed (no licensed instance available to test against).
- Webhook mode's Business Rule script template has a pseudocode HMAC line — nobody has gotten a real signed webhook working end-to-end yet.
- Adding support for a brand-new customer's custom-shaped table currently means writing a new Python mapper function (`servicenow/mappers.py`) — not self-service. See `servicenow_Design.md` §9 for the proposed fix (configurable field mapping in the UI instead of hardcoded per-table code).
- Single admin, single implicit tenant, Fernet-with-a-local-key instead of a KMS — fine for a demo portal, not for serving multiple real customers.
