"""User management (admin only): invite, change role or quota, revoke,
and grant or remove access to containers."""
import sqlite3
from datetime import datetime, timezone

import falcon

from . import audit, snapshot, validate

QUOTAS = {
    "quota_mem": validate.MAX_BYTES,   # bytes
    "quota_cpu": validate.MAX_CORES,   # cores
    "quota_disk": validate.MAX_BYTES,  # bytes
}


def _get(conn, user_id):
    row = conn.execute(
        "SELECT id, email, role, quota_mem, quota_cpu, quota_disk, created_at "
        "FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise falcon.HTTPNotFound(description="No such user.")
    user = dict(row)
    user["containers"] = [r[0] for r in conn.execute(
        "SELECT container_uuid FROM assignments WHERE user_id = ?", (user_id,))]
    return user


class UserList:
    auth = "admin"

    def on_get(self, req, resp):
        conn = req.context.db
        rows = conn.execute(
            "SELECT id, email, role, quota_mem, quota_cpu, quota_disk, created_at "
            "FROM users ORDER BY id").fetchall()
        assigned = {}
        for user_id, uuid in conn.execute(
                "SELECT user_id, container_uuid FROM assignments"):
            assigned.setdefault(user_id, []).append(uuid)
        resp.media = {"users": [
            {**dict(r), "containers": assigned.get(r["id"], [])} for r in rows]}

    def on_post(self, req, resp):
        body = validate.json_object(req, allowed={"email", "role", *QUOTAS})
        email = validate.email(body.get("email"))
        role = validate.role(body.get("role", "user"))
        quotas = {key: validate.whole_number(body.get(key, 0), key, maximum)
                  for key, maximum in QUOTAS.items()}

        conn = req.context.db
        try:
            cur = conn.execute(
                "INSERT INTO users (email, role, quota_mem, quota_cpu, quota_disk, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (email, role, quotas["quota_mem"], quotas["quota_cpu"],
                 quotas["quota_disk"], datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            raise falcon.HTTPConflict(description="This email is already invited.")

        audit.record(conn, req.context.user["email"], "user_invite", email,
                     {"role": role, **quotas})
        resp.status = falcon.HTTP_201
        resp.media = _get(conn, cur.lastrowid)


class UserItem:
    auth = "admin"

    def on_patch(self, req, resp, user_id):
        body = validate.json_object(req, allowed={"role", *QUOTAS})
        if not body:
            raise falcon.HTTPBadRequest(description="Nothing to change.")
        conn = req.context.db
        target = _get(conn, user_id)

        changes = {}
        if "role" in body:
            if user_id == req.context.user["id"]:
                raise falcon.HTTPForbidden(description="You cannot change your own role.")
            changes["role"] = validate.role(body["role"])
        for key, maximum in QUOTAS.items():
            if key in body:
                changes[key] = validate.whole_number(body[key], key, maximum)

        conn.execute(
            "UPDATE users SET role = COALESCE(?, role), "
            "quota_mem = COALESCE(?, quota_mem), quota_cpu = COALESCE(?, quota_cpu), "
            "quota_disk = COALESCE(?, quota_disk) WHERE id = ?",
            (changes.get("role"), changes.get("quota_mem"), changes.get("quota_cpu"),
             changes.get("quota_disk"), user_id))
        audit.record(conn, req.context.user["email"], "user_update",
                     target["email"], changes)
        resp.media = _get(conn, user_id)

    def on_delete(self, req, resp, user_id):
        if user_id == req.context.user["id"]:
            raise falcon.HTTPForbidden(description="You cannot revoke yourself.")
        conn = req.context.db
        target = _get(conn, user_id)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        audit.record(conn, req.context.user["email"], "user_revoke", target["email"])
        resp.status = falcon.HTTP_204


class Assignment:
    auth = "admin"

    def __init__(self, settings):
        self.data_dir = settings.data_dir

    def on_put(self, req, resp, user_id, container_id):
        conn = req.context.db
        target = _get(conn, user_id)
        names = {c["id"]: c["name"]
                 for c in snapshot.load(self.data_dir)["containers"]}
        if container_id not in names:
            raise falcon.HTTPNotFound(description="No such container.")

        conn.execute("INSERT OR IGNORE INTO containers (uuid) VALUES (?)",
                     (container_id,))
        cur = conn.execute("INSERT OR IGNORE INTO assignments VALUES (?, ?)",
                           (user_id, container_id))
        if cur.rowcount:
            audit.record(conn, req.context.user["email"], "access_grant",
                         target["email"], {"container": names[container_id],
                                           "id": container_id})
        resp.status = falcon.HTTP_204

    def on_delete(self, req, resp, user_id, container_id):
        conn = req.context.db
        target = _get(conn, user_id)
        cur = conn.execute(
            "DELETE FROM assignments WHERE user_id = ? AND container_uuid = ?",
            (user_id, container_id))
        if not cur.rowcount:
            raise falcon.HTTPNotFound(
                description="This user has no access to that container.")
        audit.record(conn, req.context.user["email"], "access_revoke",
                     target["email"], {"id": container_id})
        resp.status = falcon.HTTP_204
