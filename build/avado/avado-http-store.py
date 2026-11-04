"""Make Home Assistant trust the nginx in this container (127.0.0.1, ::1).

Edits Home Assistant's own HTTP settings store (``.storage/http``, format
version 2.2 as written by Home Assistant 2026.10). Only two settings are
touched: ``use_x_forwarded_for`` is switched on and the two loopback
addresses are added to ``trusted_proxies``; everything the owner set stays.
"""
import json
import os
import sys
from datetime import datetime, timezone

PROXIES = ["127.0.0.1/32", "::1/128"]


def now():
    return datetime.now(timezone.utc).isoformat()


def default_conf():
    # Home Assistant's defaults (HTTP_STORAGE_SCHEMA applied to {}).
    return {
        "server_port": 8123,
        "cors_allowed_origins": ["https://cast.home-assistant.io"],
        "login_attempts_threshold": -1,
        "ip_ban_enabled": True,
        "ssl_profile": "modern",
        "use_x_frame_options": True,
        "created_at": now(),
        "error": None,
        "error_message": None,
    }


def trust_local(conf):
    changed = False
    if conf.get("use_x_forwarded_for") is not True:
        conf["use_x_forwarded_for"] = True
        changed = True
    proxies = list(conf.get("trusted_proxies") or [])
    for p in PROXIES:
        if p not in proxies and p.split("/")[0] not in proxies:
            proxies.append(p)
            changed = True
    conf["trusted_proxies"] = proxies
    return changed


def main(path):
    if os.path.exists(path):
        with open(path) as f:
            store = json.load(f)
        if store.get("version") != 2:
            # A newer Home Assistant changed the format: leave it alone.
            print("[avado] HTTP settings store version %r not known; not changed" % store.get("version"))
            return 0
        data = store["data"]
        changed = trust_local(data["stable"])
        pending = data.get("pending")
        if pending and pending.get("error") is None:
            # A change the owner is trying out keeps working too.
            changed = trust_local(pending) or changed
        if not changed:
            return 0
        msg = "[avado] Home Assistant now trusts the local HTTPS front (127.0.0.1)"
    else:
        stable = default_conf()
        trust_local(stable)
        store = {
            "version": 2,
            "minor_version": 2,
            "key": "http",
            "data": {"stable": stable, "pending": None, "yaml_migration_done": True},
        }
        msg = "[avado] first start: Home Assistant set up to trust the local HTTPS front"
    tmp = path + ".avado-tmp"
    with open(tmp, "w") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, path)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
