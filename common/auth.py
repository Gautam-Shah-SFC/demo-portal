import csv
import hashlib
import secrets

import jwt

from common.paths import USERS_CSV, SECRET_FILE


def _get_secret() -> str:
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_FILE.read_text(encoding="utf-8").strip()


def _hash_password(password: str) -> str:
    return hashlib.sha256((_get_secret() + password).encode("utf-8")).hexdigest()


def ensure_seed_user() -> None:
    if USERS_CSV.exists():
        return
    with open(USERS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["email", "password_hash", "display_name"])
        writer.writerow(["john.doe@example.com", _hash_password("demo1234"), "John Doe"])


def load_users() -> dict:
    ensure_seed_user()
    users = {}
    with open(USERS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            users[row["email"].strip().lower()] = {
                "password_hash": row["password_hash"],
                "display_name": row["display_name"],
            }
    return users


def verify_login(email: str, password: str):
    users = load_users()
    user = users.get(email.strip().lower())
    if not user:
        return None
    if user["password_hash"] != _hash_password(password):
        return None
    return {"email": email.strip().lower(), "display_name": user["display_name"]}


def issue_token(email: str, display_name: str) -> str:
    payload = {"email": email.strip().lower(), "name": display_name}
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def token_for_email(email: str) -> str | None:
    users = load_users()
    user = users.get(email.strip().lower())
    if not user:
        return None
    return issue_token(email, user["display_name"])
