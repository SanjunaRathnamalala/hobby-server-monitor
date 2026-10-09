"""Host capacity, allocations and bounds for new or resized containers.
"Allocated" means the limits set on containers, not what they use now."""
import threading

import falcon

from . import units, validate

RESERVE_MEM = 2 ** 30  # 1 GiB kept for the host itself
MINIMUM = {"mem": 128 * 2 ** 20, "cpu": 1, "disk": 2 * 2 ** 30}
LIMIT_DRIVERS = ("btrfs", "zfs", "lvm")  # pool drivers that enforce disk size
PROCESS_LIMIT = 500

# One create or resize at a time, so two requests cannot both pass the check
# before either container exists. Works because the API runs as one process.
lock = threading.Lock()


def read_state(lxd):
    """Host totals, limit-capable pools, and every container's limits."""
    res = lxd.api.resources.get().json()["metadata"]
    host_mem = res["memory"]["total"]
    host = {"mem": host_mem - RESERVE_MEM, "cpu": res["cpu"]["total"]}

    pools = {}
    for pool in lxd.storage_pools.all():
        if pool.driver in LIMIT_DRIVERS:
            info = lxd.api.storage_pools[pool.name].resources.get().json()["metadata"]
            pools[pool.name] = info["space"]["total"]

    containers = []
    for inst in lxd.instances.all():
        cfg = inst.expanded_config
        root = inst.expanded_devices.get("root") or {}
        containers.append({
            "id": cfg.get("volatile.uuid"),
            "name": inst.name,
            "pool": root.get("pool"),
            "mem": units.parse_bytes(cfg.get("limits.memory"), host_mem),
            "cpu": units.parse_cpu(cfg.get("limits.cpu")),
            "disk": units.parse_bytes(root.get("size")),
        })
    return host, pools, containers


def choices(lxd):
    """Local images and LXD-managed bridges the create form may offer."""
    images = {i.fingerprint: i.properties.get("description", i.fingerprint[:12])
              for i in lxd.images.all()}
    networks = [n.name for n in lxd.networks.all()
                if n.managed and n.type == "bridge"]
    return images, networks


def total(rows, key):
    return sum(row[key] or 0 for row in rows)


def owned_ids(conn, owner_id):
    return {r[0] for r in conn.execute(
        "SELECT uuid FROM containers WHERE owner_id = ?", (owner_id,))}


def owner_quota(conn, owner_id):
    """The owner's quota row, or None if no quota applies (no owner, or an admin)."""
    if owner_id is None:
        return None
    row = conn.execute("SELECT role, quota_mem, quota_cpu, quota_disk FROM users "
                       "WHERE id = ?", (owner_id,)).fetchone()
    if row is None:
        raise falcon.HTTPBadRequest(description="owner_id is not a known user.")
    return row if row["role"] == "user" else None


def bounds(conn, state, owner_id, exclude_id=None):
    """Largest RAM, cores and disk (per pool) a container may get.
    exclude_id: a container being resized; its current limits are given back."""
    host, pools, containers = state
    others = [c for c in containers if c["id"] != exclude_id]
    limits = {
        "mem": host["mem"] - total(others, "mem"),
        "cpu": host["cpu"] - total(others, "cpu"),
        "disk": {name: size - total([c for c in others if c["pool"] == name], "disk")
                 for name, size in pools.items()},
    }
    quota = owner_quota(conn, owner_id)
    if quota is not None:
        owned = owned_ids(conn, owner_id)
        mine = [c for c in others if c["id"] in owned]
        limits["mem"] = min(limits["mem"], quota["quota_mem"] - total(mine, "mem"))
        limits["cpu"] = min(limits["cpu"], quota["quota_cpu"] - total(mine, "cpu"))
        disk_left = quota["quota_disk"] - total(mine, "disk")
        limits["disk"] = {name: min(left, disk_left)
                          for name, left in limits["disk"].items()}
    return limits


def check(key, value, maximum):
    """Validate a requested limit against its minimum and the bound."""
    if maximum < MINIMUM[key]:
        raise falcon.HTTPConflict(
            description=f"Not enough {key} left (host capacity or owner's quota).")
    return validate.whole_number(value, key, maximum, MINIMUM[key])
