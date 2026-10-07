"""Read the collector's snapshot file and judge how fresh it is."""
import json
from datetime import datetime, timezone
from pathlib import Path

STALE_AFTER = 30  # seconds: three missed collector rounds


def load(data_dir):
    path = Path(data_dir) / "snapshot.json"
    try:
        with open(path) as f:
            snap = json.load(f)
    except FileNotFoundError:
        return {"collector": "no_data", "lxd_ok": False,
                "last_success": None, "containers": []}

    last_attempt = datetime.fromisoformat(snap["last_attempt"])
    age = (datetime.now(timezone.utc) - last_attempt).total_seconds()
    return {
        "collector": "ok" if age <= STALE_AFTER else "stopped",
        "lxd_ok": snap["error"] is None,
        "last_success": snap["last_success"],
        "containers": snap["containers"],
    }
