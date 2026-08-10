import time

import requests

from common.crypto import decrypt

TOKEN_PATH = "/oauth_token.do"
TOKEN_EXPIRY_SAFETY_MARGIN_SEC = 30


class ServiceNowAuthError(Exception):
    pass


class ServiceNowAuthClient:
    """
    Hands back either a Bearer token (oauth2) or a (username, password) pair
    (basic) for a connection, uniformly, so callers never branch on auth_type
    themselves. OAuth2 here is the Resource Owner Password Credentials grant
    (username+password+client_id+client_secret -> access_token+refresh_token)
    rather than the Authorization Code grant -- deliberately, since the design
    doc's own testing notes flag that ServiceNow's redirect-based OAuth needs a
    reachable HTTPS callback (ngrok, not localhost); password grant needs no
    redirect at all and still gives a real refresh-token lifecycle.

    Tokens are cached in-memory per connection id and refreshed on expiry, or
    via a fresh password grant if the refresh token itself has gone stale.
    """

    def __init__(self):
        self._token_cache: dict[str, dict] = {}

    def get_auth(self, connection: dict) -> dict:
        if connection.get("auth_type") == "basic":
            username = connection.get("username", "")
            password = decrypt(connection.get("password_encrypted", ""))
            return {"basic": (username, password)}
        return {"headers": {"Authorization": f"Bearer {self._get_access_token(connection)}"}}

    def invalidate(self, connection_id: str) -> None:
        self._token_cache.pop(connection_id, None)

    def _get_access_token(self, connection: dict) -> str:
        cid = connection["id"]
        cached = self._token_cache.get(cid)
        now = time.time()
        if cached and cached["expires_at"] > now + TOKEN_EXPIRY_SAFETY_MARGIN_SEC:
            return cached["access_token"]

        if cached and cached.get("refresh_token"):
            token = self._refresh(connection, cached["refresh_token"])
        else:
            token = self._password_grant(connection)

        self._token_cache[cid] = token
        return token["access_token"]

    def _password_grant(self, connection: dict) -> dict:
        payload = {
            "grant_type": "password",
            "client_id": connection.get("client_id", ""),
            "client_secret": decrypt(connection.get("client_secret_encrypted", "")),
            "username": connection.get("username", ""),
            "password": decrypt(connection.get("password_encrypted", "")),
        }
        return self._request_token(connection, payload)

    def _refresh(self, connection: dict, refresh_token: str) -> dict:
        payload = {
            "grant_type": "refresh_token",
            "client_id": connection.get("client_id", ""),
            "client_secret": decrypt(connection.get("client_secret_encrypted", "")),
            "refresh_token": refresh_token,
        }
        try:
            return self._request_token(connection, payload)
        except ServiceNowAuthError:
            return self._password_grant(connection)

    def _request_token(self, connection: dict, payload: dict) -> dict:
        url = connection["instance_url"].rstrip("/") + TOKEN_PATH
        try:
            resp = requests.post(url, data=payload, timeout=15)
        except requests.exceptions.RequestException as exc:
            raise ServiceNowAuthError(f"Token request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise ServiceNowAuthError(f"Token request failed: HTTP {resp.status_code} {resp.text[:300]}")
        body = resp.json()
        return {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + int(body.get("expires_in", 1800)),
        }


auth_client = ServiceNowAuthClient()
