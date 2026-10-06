"""SQLite access: connections and schema setup."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role        TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    quota_mem   INTEGER NOT NULL DEFAULT 0,   -- bytes
    quota_cpu   INTEGER NOT NULL DEFAULT 0,   -- cores
    quota_disk  INTEGER NOT NULL DEFAULT 0,   -- bytes
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS containers (
    uuid      TEXT PRIMARY KEY,
    owner_id  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    container_uuid  TEXT NOT NULL REFERENCES containers(uuid) ON DELETE CASCADE,
    PRIMARY KEY (user_id, container_uuid)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    time         TEXT NOT NULL,
    actor_email  TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    details      TEXT
);
"""


def connect(path):
    """Open a connection. Foreign keys must be switched on per connection."""
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path):
    """Create the database and tables if needed. Safe to run many times."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        return SCHEMA_VERSION
    finally:
        conn.close()
