"""Login sessions stored in SQLite.

The browser holds a random token in a cookie. The database stores only
a SHA-256 hash of it, so a leaked database file gives no working sessions.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

COOKIE = "hsm_session"
LIFETIME = timedelta(hours=12)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def create(conn, user_id):
    """Start a session. Returns the token (for the cookie) and expiry time."""
    token = secrets.token_urlsafe(32)
    expires = _now() + LIFETIME
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash(token), user_id, expires.isoformat()),
    )
    return token, expires


def lookup(conn, token):
    """Return the logged-in user (id, email, role), or None."""
    if not token:
        return None
    return conn.execute(
        """SELECT u.id, u.email, u.role
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_hash(token), _now().isoformat()),
    ).fetchone()


def delete(conn, token):
    """Log out: the token stops working immediately."""
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def delete_expired(conn):
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))
