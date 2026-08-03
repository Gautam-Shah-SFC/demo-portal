import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from common.store import init_db, insert_records

app = FastAPI(title="Demo Portal Ingest")
init_db()


@app.post("/api/ingest/activity")
async def ingest_activity(request: Request, authorization: str | None = Header(default=None)):
    payload = await request.json()
    records = payload.get("records", [])
    if not isinstance(records, list):
        return JSONResponse(status_code=400, content={"status": "error", "message": "records must be a list"})

    count = insert_records(records)
    return {"status": "ok", "received": count}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
