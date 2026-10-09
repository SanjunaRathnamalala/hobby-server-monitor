"""Container endpoints, served from the collector's snapshot and LXD."""
from datetime import datetime, timezone

import falcon

from . import audit, capacity, history, lxd, snapshot, validate

ACTIONS = {
    "start": lambda inst: inst.start(wait=True),
    "stop": lambda inst: inst.stop(timeout=lxd.STOP_TIMEOUT, force=False, wait=True),
    "force_stop": lambda inst: inst.stop(force=True, wait=True),
    "restart": lambda inst: inst.restart(timeout=lxd.STOP_TIMEOUT, force=False, wait=True),
    "freeze": lambda inst: inst.freeze(wait=True),
    "unfreeze": lambda inst: inst.unfreeze(wait=True),
}

CREATE_FIELDS = {"name", "image", "pool", "network", "mem", "cpu", "cpu_allowance",
                 "disk", "owner_id", "ephemeral", "autostart", "description"}
LIMIT_FIELDS = {"mem", "cpu", "cpu_allowance", "disk"}

EXEC_TIMEOUT = 10        # seconds
EXEC_MAX_OUTPUT = 65536  # bytes
# The user's command arrives as $1, so it can never change this wrapper.
# PIPESTATUS keeps the command's own exit code, not the exit code of head.
EXEC_WRAPPER = f'bash -c "$1" 2>&1 | head -c {EXEC_MAX_OUTPUT}; exit ${{PIPESTATUS[0]}}'

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
    auth = {"GET": "user", "POST": "admin"}

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


    def on_post(self, req, resp):
        body = validate.json_object(req, allowed=CREATE_FIELDS)
        name = validate.container_name(body.get("name"))
        owner_id = body.get("owner_id")
        if owner_id is not None:
            owner_id = validate.whole_number(owner_id, "owner_id", 2 ** 62)
        allowance = validate.whole_number(body.get("cpu_allowance", 100),
                                          "cpu_allowance", 100, 1)
        ephemeral = validate.boolean(body.get("ephemeral", False), "ephemeral")
        autostart = validate.boolean(body.get("autostart", False), "autostart")
        description = validate.text(body.get("description", ""), "description", 200)
        conn = req.context.db

        with lxd.errors(), capacity.lock:
            client = lxd.client()
            images, networks = capacity.choices(client)
            if body.get("image") not in images:
                raise falcon.HTTPBadRequest(description="image is not available.")
            if body.get("network") not in networks:
                raise falcon.HTTPBadRequest(description="network is not available.")
            limits = capacity.bounds(conn, capacity.read_state(client), owner_id)
            pool = body.get("pool")
            if pool not in limits["disk"]:
                raise falcon.HTTPBadRequest(description="pool is not available.")
            mem = capacity.check("mem", body.get("mem"), limits["mem"])
            cpu = capacity.check("cpu", body.get("cpu"), limits["cpu"])
            disk = capacity.check("disk", body.get("disk"), limits["disk"][pool])

            client.instances.create({
                "name": name,
                "source": {"type": "image", "fingerprint": body["image"]},
                "profiles": ["default"],
                "ephemeral": ephemeral,
                "description": description,
                "config": {
                    "limits.memory": str(mem),
                    "limits.cpu": str(cpu),
                    "limits.cpu.allowance": f"{allowance}%",
                    "limits.processes": str(capacity.PROCESS_LIMIT),
                    "boot.autostart": "true" if autostart else "false",
                },
                "devices": {
                    "root": {"type": "disk", "path": "/", "pool": pool, "size": str(disk)},
                    "eth0": {"type": "nic", "name": "eth0", "network": body["network"]},
                },
            }, wait=True)
            inst = client.instances.get(name)
            uuid = inst.config["volatile.uuid"]

            conn.execute("INSERT INTO containers (uuid, owner_id) VALUES (?, ?)",
                         (uuid, owner_id))
            if owner_id is not None:
                conn.execute("INSERT INTO assignments VALUES (?, ?)", (owner_id, uuid))
            audit.record(conn, req.context.user["email"], "container_create", name,
                         {"id": uuid, "owner_id": owner_id, "mem": mem, "cpu": cpu,
                          "disk": disk, "pool": pool})
            conn.commit()  # ownership must be saved before the lock is released

        started = lxd.start_quietly(inst)
        resp.status = falcon.HTTP_201
        resp.media = {"id": uuid, "name": name, "started": started}


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

class ContainerOptions:
    """Everything the create form needs, with bounds for the chosen owner."""
    auth = "admin"

    def on_get(self, req, resp):
        owner_id = req.get_param_as_int("owner")
        conn = req.context.db
        with lxd.errors():
            client = lxd.client()
            images, networks = capacity.choices(client)
            limits = capacity.bounds(conn, capacity.read_state(client), owner_id)
        minimum = capacity.MINIMUM
        resp.media = {
            "images": [{"fingerprint": fp, "description": d} for fp, d in images.items()],
            "networks": networks,
            "pools": [{"name": n, "max_disk": left} for n, left in limits["disk"].items()],
            "mem": {"min": minimum["mem"], "max": limits["mem"]},
            "cpu": {"min": minimum["cpu"], "max": limits["cpu"]},
            "disk_min": minimum["disk"],
            "cpu_allowance": {"min": 1, "max": 100},
            "owners": [dict(r) for r in conn.execute(
                "SELECT id, email FROM users WHERE role = 'user' ORDER BY email")],
        }


class ContainerLimits:
    auth = "admin"

    def on_patch(self, req, resp, container_id):
        body = validate.json_object(req, allowed=LIMIT_FIELDS)
        if not body:
            raise falcon.HTTPBadRequest(description="Nothing to change.")
        conn = req.context.db
        row = conn.execute("SELECT owner_id FROM containers WHERE uuid = ?",
                           (container_id,)).fetchone()
        owner_id = row["owner_id"] if row else None

        with lxd.errors(), capacity.lock:
            client = lxd.client()
            inst = lxd.find(client, container_id)
            limits = capacity.bounds(conn, capacity.read_state(client), owner_id,
                                     exclude_id=container_id)
            changes = {}
            if "mem" in body:
                changes["limits.memory"] = str(capacity.check("mem", body["mem"], limits["mem"]))
            if "cpu" in body:
                changes["limits.cpu"] = str(capacity.check("cpu", body["cpu"], limits["cpu"]))
            if "cpu_allowance" in body:
                pct = validate.whole_number(body["cpu_allowance"], "cpu_allowance", 100, 1)
                changes["limits.cpu.allowance"] = f"{pct}%"
            inst.config.update(changes)
            if "disk" in body:
                root = dict(inst.expanded_devices.get("root") or {})
                if root.get("pool") not in limits["disk"]:
                    raise falcon.HTTPConflict(
                        description="This container's pool cannot limit disk size.")
                root["size"] = str(capacity.check("disk", body["disk"],
                                                  limits["disk"][root["pool"]]))
                inst.devices["root"] = root
                changes["root.size"] = root["size"]
            inst.save(wait=True)

        audit.record(conn, req.context.user["email"], "container_limits", inst.name,
                     {"id": container_id, **changes})
        resp.media = {"name": inst.name, "changes": changes}

class ContainerExec:
    """Run one command inside a container and return its output."""
    auth = "user"  # the middleware also checks the container is assigned

    def on_post(self, req, resp, container_id):
        body = validate.json_object(req, allowed={"command"})
        command = validate.text(body.get("command"), "command", 1000)
        if not command.strip():
            raise falcon.HTTPBadRequest(description="command is empty.")

        with lxd.errors():
            inst = lxd.find(lxd.client(), container_id)
            if inst.status != "Running":
                raise falcon.HTTPConflict(description="The container is not running.")
            result = inst.execute(["timeout", str(EXEC_TIMEOUT), "bash", "-c",
                                   EXEC_WRAPPER, "bash", command])

        audit.record(req.context.db, req.context.user["email"], "exec", inst.name,
                     {"id": container_id, "command": command[:200],
                      "exit_code": result.exit_code})
        resp.media = {"exit_code": result.exit_code, "output": result.stdout,
                      "timed_out": result.exit_code == 124}
