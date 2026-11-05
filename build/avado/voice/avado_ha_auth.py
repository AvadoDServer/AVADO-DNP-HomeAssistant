"""Give the AVADO voice helper its own way into Home Assistant's API.

Runs before Home Assistant starts (app-prestart.sh), like avado-http-store.py. It makes sure Home
Assistant's user store (/config/.storage/auth) holds one system user "AVADO" (an administrator,
local only, hidden from Settings > People, like the users Home Assistant makes for its own add-ons)
with one system refresh token, and keeps that token's id and signing key in /config/.avado/ha-api.json
(mode 600). The helper signs its own short access tokens with it (avado_voice.py, HS256, 30 minutes),
exactly as Home Assistant checks them. Nothing else in the store is changed.

* No store yet (Home Assistant has never started): a new store with the three default groups and the
  AVADO user; Home Assistant's first-run set-up is unaffected (it does not count system users).
* A store of an unknown format: left alone (the voice switch then says it cannot reach Home Assistant).
"""
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone

STORE = "/config/.storage/auth"
OURS = "/config/.avado/ha-api.json"
USER_NAME = "AVADO"
DEFAULT_GROUPS = [
    {"id": "system-admin", "name": "Administrators"},
    {"id": "system-users", "name": "Users"},
    {"id": "system-read-only", "name": "Read Only"},
]


def now():
    return datetime.now(timezone.utc).isoformat()


def write_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".avado-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def ensure(store_path=STORE, ours_path=OURS, version="2026.11.0"):
    """Returns a short sentence for the log, or None when nothing had to change."""
    store = load(store_path)
    if os.path.exists(store_path) and store is None:
        return "the user store could not be read; not changed"
    if store is None:
        store = {"version": 1, "minor_version": 1, "key": "auth",
                 "data": {"users": [], "groups": [dict(g) for g in DEFAULT_GROUPS], "credentials": [],
                          "refresh_tokens": []}}
        created = True
    else:
        created = False
        if store.get("version") != 1 or not isinstance(store.get("data"), dict):
            return "the user store has a format this version does not know; not changed"
    data = store["data"]
    for k in ("users", "credentials", "refresh_tokens"):
        if not isinstance(data.get(k), list):
            return "the user store has a format this version does not know; not changed"
    if not isinstance(data.get("groups"), list):
        data["groups"] = [dict(g) for g in DEFAULT_GROUPS]

    ours = load(ours_path) or {}
    user = next((u for u in data["users"] if u.get("id") == ours.get("user_id")), None) or \
        next((u for u in data["users"] if u.get("name") == USER_NAME and u.get("system_generated")), None)
    token = next((t for t in data["refresh_tokens"] if t.get("id") == ours.get("refresh_token_id")
                  and t.get("user_id") == ours.get("user_id")), None)
    if user and token and token.get("jwt_key") == ours.get("jwt_key") and user.get("is_active", True):
        return None

    if not user:
        user = {"id": uuid.uuid4().hex, "group_ids": ["system-admin"], "is_owner": False, "is_active": True,
                "name": USER_NAME, "system_generated": True, "local_only": True}
        data["users"].append(user)
    user["is_active"] = True
    if "system-admin" not in (user.get("group_ids") or []):
        user["group_ids"] = list(user.get("group_ids") or []) + ["system-admin"]
    if not any(g.get("id") == "system-admin" for g in data["groups"]):
        data["groups"].append(dict(DEFAULT_GROUPS[0]))
    # A token of ours that lost its key file: replace it
    data["refresh_tokens"] = [t for t in data["refresh_tokens"] if t.get("user_id") != user["id"]]
    token = {
        "id": uuid.uuid4().hex, "user_id": user["id"], "client_id": None, "client_name": None,
        "client_icon": None, "token_type": "system", "created_at": now(), "access_token_expiration": 1800.0,
        "token": secrets.token_hex(64), "jwt_key": secrets.token_hex(64), "last_used_at": None,
        "last_used_ip": None, "expire_at": None, "credential_id": None, "version": version,
    }
    data["refresh_tokens"].append(token)
    write_private(store_path, store)
    write_private(ours_path, {"user_id": user["id"], "refresh_token_id": token["id"], "jwt_key": token["jwt_key"]})
    return "new user store with the AVADO system user" if created else "AVADO system user ready"


def main():
    msg = ensure()
    if msg:
        print("[avado] voice helper: %s" % msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
