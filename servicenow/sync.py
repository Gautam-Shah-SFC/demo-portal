import logging
import time
from datetime import datetime, timezone

from common.servicenow_store import get_connection, set_status, set_watermark, touch_synced
from common.store import upsert_record
from servicenow.http import get as sn_get
from servicenow.mappers import MAPPERS, SUBTYPE_FOR_TABLE

logger = logging.getLogger("ServiceNowSync")

PAGE_SIZE = 500
MAX_RETRIES = 3
EPOCH_WATERMARK = "1970-01-01 00:00:00"


def sync_connection(connection_id: str) -> None:
    connection = get_connection(connection_id)
    if not connection or connection.get("status") == "paused":
        return

    had_error = False
    for table in connection.get("tables_enabled", []):
        mapper = MAPPERS.get(table)
        if not mapper:
            logger.warning(f"No mapper registered for table {table!r}; skipping.")
            continue
        try:
            _sync_table(connection, table, mapper)
        except Exception as e:
            had_error = True
            logger.error(f"ServiceNow sync failed for connection {connection_id} table {table}: {e}")
            set_status(connection_id, "error", last_error=str(e))

    if not had_error:
        set_status(connection_id, "active", last_error=None)
    touch_synced(connection_id)


def _sync_table(connection: dict, table: str, mapper) -> None:
    watermark = connection.get("last_sync_watermark", {}).get(table) or EPOCH_WATERMARK
    offset = 0
    max_seen = watermark
    subtype = SUBTYPE_FOR_TABLE.get(table, table)

    while True:
        params = {
            "sysparm_query": f"sys_created_on>{watermark}^ORDERBYsys_created_on",
            "sysparm_limit": PAGE_SIZE,
            "sysparm_offset": offset,
            # Deliberately NOT sysparm_display_value=true -- that returns
            # choice fields (e.g. sys_cs_message.direction) as capitalized
            # display text ("Outbound") instead of the raw internal value
            # ("outbound") mappers match against, and display text is
            # locale-dependent besides. Raw values only.
        }
        resp = _get_with_retry(connection, f"/api/now/table/{table}", params)
        rows = resp.json().get("result", [])
        if not rows:
            break

        for row in rows:
            # Track watermark progress against every fetched row, not just
            # ones the mapper keeps -- otherwise a page that's entirely
            # filtered-out noise (e.g. Virtual Agent's UI-control messages)
            # never advances the watermark and gets re-fetched forever.
            created = row.get("sys_created_on")
            if isinstance(created, dict):
                created = created.get("value") or created.get("display_value")
            if created and created > max_seen:
                max_seen = created

            mapped = mapper(row)
            if mapped is None:
                continue
            sys_id = row.get("sys_id")
            if not sys_id:
                continue

            record = {
                "id": sys_id,
                "provider": "servicenow",
                "provider_display_name": "ServiceNow",
                "source_subtype": subtype,
                "prompt": mapped.get("prompt", ""),
                "response": mapped.get("response", ""),
                "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            # Upsert, not insert -- overlapping poll windows and retried
            # batches are expected and must never create duplicate rows
            # (servicenow_Design.md §3/§4). Scoped to this connection, not
            # just sys_id -- sys_id is only unique *within* one ServiceNow
            # instance, so two different connections could otherwise collide
            # on the same external_id and overwrite each other's rows.
            upsert_record(record, source_type="servicenow", external_id=f"{connection['id']}:{sys_id}")

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    # Advanced once at the end rather than per-page: upsert_record's dedup
    # already makes a re-fetched page harmless, so a crash mid-pagination
    # just costs a redundant re-fetch next run, never a lost or duplicated
    # row -- no need for finer-grained watermark commits.
    if max_seen != watermark:
        set_watermark(connection["id"], table, max_seen)


def _get_with_retry(connection: dict, path: str, params: dict):
    delay = 1.0
    last_exc = None
    for _ in range(MAX_RETRIES):
        try:
            resp = sn_get(connection, path, params)
        except Exception as e:
            last_exc = e
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else delay)
            delay *= 2
            last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")
            continue
        resp.raise_for_status()
        return resp
    raise last_exc or Exception("ServiceNow request failed after retries")
