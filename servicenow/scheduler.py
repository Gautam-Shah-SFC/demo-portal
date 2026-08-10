import asyncio
import logging

from common.servicenow_store import list_connections
from servicenow.sync import sync_connection

logger = logging.getLogger("ServiceNowScheduler")

DEFAULT_INTERVAL_SEC = 60
TICK_SEC = 5


async def run_forever(stop_event: asyncio.Event) -> None:
    logger.info("ServiceNow poll scheduler started.")
    last_run: dict[str, float] = {}
    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        now = loop.time()
        for connection in list_connections():
            if connection.get("sync_mode") != "poll" or connection.get("status") == "paused":
                continue
            interval = connection.get("poll_interval_sec") or DEFAULT_INTERVAL_SEC
            cid = connection["id"]
            if now - last_run.get(cid, 0) >= interval:
                last_run[cid] = now
                try:
                    # sync_connection is blocking (requests); run off the
                    # event loop so it never stalls the WS/ingest routes
                    # this scheduler shares a process with.
                    await asyncio.to_thread(sync_connection, cid)
                except Exception as e:
                    logger.error(f"Unhandled error syncing connection {cid}: {e}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TICK_SEC)
        except asyncio.TimeoutError:
            pass

    logger.info("ServiceNow poll scheduler stopped.")
