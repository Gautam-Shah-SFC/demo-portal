#!/usr/bin/env bash
set -euo pipefail

INGEST_PORT="${1:-8000}"
PORTAL_PORT="${2:-8501}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"

echo "Starting ingest server on port $INGEST_PORT..."
"$PYTHON" -m uvicorn ingest_server.main:app --host 0.0.0.0 --port "$INGEST_PORT" &
INGEST_PID=$!

cleanup() {
    echo "Stopping ingest server..."
    kill "$INGEST_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting Streamlit portal on port $PORTAL_PORT..."
"$PYTHON" -m streamlit run "$SCRIPT_DIR/Home.py" --server.port "$PORTAL_PORT" --server.address 0.0.0.0
