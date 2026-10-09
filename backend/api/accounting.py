"""Usage accounting: host totals against allocations, and quotas."""
from . import capacity, lxd

KEYS = ("mem", "cpu", "disk")


def _allocated(rows):
    return {key: capacity.total(rows, key) for key in KEYS}


def _quota(row):
    return {"mem": row["quota_mem"], "cpu": row["quota_cpu"], "disk": row["quota_disk"]}


class Capacity:
    auth = "admin"

    def on_get(self, req, resp):
        conn = req.context.db
        with lxd.errors():
            host, pools, containers = capacity.read_state(lxd.client())
        owners = {r[0]: r[1] for r in conn.execute("SELECT uuid, owner_id FROM containers")}
        users = []
        for u in conn.execute("SELECT id, email, quota_mem, quota_cpu, quota_disk "
                              "FROM users WHERE role = 'user' ORDER BY email"):
            mine = [c for c in containers if owners.get(c["id"]) == u["id"]]
            users.append({"id": u["id"], "email": u["email"],
                          "quota": _quota(u), "allocated": _allocated(mine)})
        resp.media = {
            "host": {"mem": host["mem"], "cpu": host["cpu"], "disk": pools,
                     "reserve_mem": capacity.RESERVE_MEM},
            "allocated": {
                "mem": capacity.total(containers, "mem"),
                "cpu": capacity.total(containers, "cpu"),
                "disk": {name: capacity.total([c for c in containers if c["pool"] == name],
                                              "disk") for name in pools},
            },
            "unlimited": [c["name"] for c in containers
                          if c["mem"] is None or c["cpu"] is None],
            "users": users,
        }


class MyQuota:
    auth = "user"

    def on_get(self, req, resp):
        conn, user = req.context.db, req.context.user
        row = conn.execute("SELECT quota_mem, quota_cpu, quota_disk FROM users "
                           "WHERE id = ?", (user["id"],)).fetchone()
        with lxd.errors():
            _, _, containers = capacity.read_state(lxd.client())
        owned = capacity.owned_ids(conn, user["id"])
        resp.media = {"quota": _quota(row),
                      "allocated": _allocated([c for c in containers if c["id"] in owned])}
