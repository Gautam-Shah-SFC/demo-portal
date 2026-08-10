import requests

from servicenow.auth_client import auth_client


def get(connection: dict, path: str, params: dict | None = None):
    auth = auth_client.get_auth(connection)
    url = connection["instance_url"].rstrip("/") + path
    kwargs = {"params": params or {}, "timeout": 20}
    if "headers" in auth:
        kwargs["headers"] = auth["headers"]
    if "basic" in auth:
        kwargs["auth"] = auth["basic"]
    return requests.get(url, **kwargs)
