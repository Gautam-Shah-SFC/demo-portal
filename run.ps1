param(
    [int]$IngestPort = 8000,
    [int]$PortalPort = 8501
)

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

Write-Host "Starting ingest server on port $IngestPort..."
$ingest = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","ingest_server.main:app","--host","0.0.0.0","--port","$IngestPort" -PassThru -WindowStyle Hidden

try {
    Write-Host "Starting Streamlit portal on port $PortalPort..."
    & $python -m streamlit run Home.py --server.port $PortalPort --server.address 0.0.0.0
}
finally {
    Write-Host "Stopping ingest server..."
    Stop-Process -Id $ingest.Id -Force -ErrorAction SilentlyContinue
}
