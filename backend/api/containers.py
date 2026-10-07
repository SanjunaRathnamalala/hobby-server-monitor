"""Container endpoints, served from the collector's snapshot and LXD."""
from datetime import datetime, timezone

import falcon

from . import audit, history, lxd, snapshot

ACTIONS = {
    "start": lambda inst: inst.start(wait=True),
    "stop": lambda inst: inst.stop(timeout=lxd.STOP_TIMEOUT, force=False, wait=True),
    "force_stop": lambda inst: inst.stop(force=True, wait=True),
    "restart": lambda inst: inst.restart(timeout=lxd.STOP_TIMEOUT, force=False, wait=True),
    "freeze": lambda inst: inst.freeze(wait=True),
    "unfreeze": lambda inst: inst.unfreeze(wait=True),
}


def _uptime(container, now):
    """Seconds since the container started, or None if it is not running."""
    started = container.get("last_used_at")
    if container["status"] != "Running" or not started:
        return None
    # LXD gives nanoseconds; whole seconds are enough here.
    t = datetime.strptime(started[:19], "%Y-%m-%dT%H:%M:%S")
    return max(0, int((now - t.replace(tzinfo=timezone.utc)).total_seconds()))


def _status(snap):
    return {key: snap[key] for key in ("collector", "lxd_ok", "last_success")}


class ContainerList:
    auth = "user"

    def __init__(self, settings):
        self.data_dir = settings.data_dir

    def on_get(self, req, resp):
        snap = snapshot.load(self.data_dir)
        user = req.context.user
        containers = snap["containers"]
        if user["role"] != "admin":
            rows = req.context.db.execute(
                "SELECT container_uuid FROM assignments WHERE user_id = ?",
                (user["id"],),
            )
            allowed = {row[0] for row in rows}
            containers = [c for c in containers if c["id"] in allowed]

        now = datetime.now(timezone.utc)
        resp.media = {
            "status": _status(snap),
            "containers": [{**c, "uptime": _uptime(c, now)} for c in containers],
        }


class ContainerItem:
    auth = {"GET": "user", "DELETE": "admin"}

    def __init__(self, settings):
        self.data_dir = settings.data_dir

    def on_get(self, req, resp, container_id):
        snap = snapshot.load(self.data_dir)
        for c in snap["containers"]:
            if c["id"] == container_id:
                now = datetime.now(timezone.utc)
                resp.media = {
                    "status": _status(snap),
                    "container": {**c, "uptime": _uptime(c, now)},
                }
                return
        raise falcon.HTTPNotFound()

    def on_delete(self, req, resp, container_id):
        with lxd.errors():
            inst = lxd.find(lxd.client(), container_id)
            if req.get_param("confirm") != inst.name:
                raise falcon.HTTPBadRequest(
                    description="To delete, add confirm=<container name>."
                )
            if inst.status != "Stopped":
                inst.stop(force=True, wait=True)
            inst.delete(wait=True)
        conn = req.context.db
        conn.execute("DELETE FROM containers WHERE uuid = ?", (container_id,))
        audit.record(
            conn,
            req.context.user["email"],
            "container_delete",
            inst.name,
            {"id": container_id},
        )
        resp.status = falcon.HTTP_204


class ContainerHistory:
    auth = "user"

    def __init__(self, settings):
        self.data_dir = settings.data_dir

    def on_get(self, req, resp, container_id):
        range_name = req.get_param("range", default="1h")
        if range_name not in history.RANGES:
            raise falcon.HTTPBadRequest(
                description="range must be one of: " + ", ".join(history.RANGES)
            )
        resp.media = history.query(self.data_dir, container_id, range_name)


class ContainerAction:
    auth = "admin"

    def on_post(self, req, resp, container_id):
        body = req.get_media()
        action = body.get("action") if isinstance(body, dict) else None
        if action not in ACTIONS:
            raise falcon.HTTPBadRequest(
                description="action must be one of: " + ", ".join(ACTIONS)
            )
        with lxd.errors():
            inst = lxd.find(lxd.client(), container_id)
            ACTIONS[action](inst)
        audit.record(
            req.context.db,
            req.context.user["email"],
            f"container_{action}",
            inst.name,
            {"id": container_id},
        )
        resp.media = {"action": action, "name": inst.name}
