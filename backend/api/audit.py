"""Audit trail for actions that change containers or users."""
import json
from datetime import datetime, timezone


def record(conn, actor_email, action, target, details=None):
    conn.execute(
        "INSERT INTO audit_log (time, actor_email, action, target, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), actor_email, action, target,
         json.dumps(details, separators=(",", ":")) if details else None),
    )
