from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_CSV = DATA_DIR / "users.csv"
SECRET_FILE = DATA_DIR / "secret.key"
DEVICES_JSON = DATA_DIR / "devices.json"
INGEST_DB = DATA_DIR / "ingest.db"
