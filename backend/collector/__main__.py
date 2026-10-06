"""Metrics collector

Reads all LXD containers every 10 seconds, stores the values in
TinyFlux and writes a small snapshot file for the API.
"""
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from . import lxd_reader, metrics, snapshot
from .storage import MetricStore

INTERVAL = 10  # seconds, required by the task

log = logging.getLogger("collector")


class Collector:
    def __init__(self, data_dir):
        self.store = MetricStore(data_dir)
        self.snapshot_path = Path(data_dir) / "snapshot.json"
        self.client = None
        self.previous = {}          # container id -> last reading
        self.containers = []        # last known list, for the snapshot
        self.last_success = None
        self.lxd_ok = None          # None = unknown at start
        self.stopping = False

    def stop(self, signum, frame):
        log.info("stop requested, finishing current round")
        self.stopping = True

    def run_round(self, now):
        try:
            if self.client is None:
                self.client = lxd_reader.connect()
            readings = lxd_reader.read_all(self.client)
        except Exception as exc:
            if self.lxd_ok is not False:
                log.warning("LXD not reachable: %s", exc)
            self.lxd_ok = False
            self.client = None
            self.write_snapshot(now, error=str(exc))
            return

        if self.lxd_ok is not True:
            log.info("LXD reachable, collecting")
        self.lxd_ok = True

        rows = {}
        containers = []
        for r in readings:
            fields = metrics.compute(self.previous.get(r["id"]), r)
            if r["status"] == "Running":
                rows[r["id"]] = fields
            containers.append({
                "id": r["id"], "name": r["name"], "status": r["status"],
                "ipv4": r["ipv4"], "image": r["image"],
                "last_used_at": r["last_used_at"],
                "rx_bytes": r["rx_bytes"], "tx_bytes": r["tx_bytes"],
                **fields,
            })

        self.previous = {r["id"]: r for r in readings}
        self.containers = containers
        self.last_success = now
        self.store.write(now, rows)
        self.write_snapshot(now, error=None)

    def write_snapshot(self, now, error):
        snapshot.write(self.snapshot_path, {
            "last_attempt": now.isoformat(),
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "error": error,
            "containers": self.containers,
        })

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        next_run = time.monotonic()
        cleaned_hour = None

        while not self.stopping:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            try:
                self.run_round(now)
                if now.hour != cleaned_hour:
                    self.store.delete_old_files(now)
                    cleaned_hour = now.hour
            except OSError as exc:
                log.error("could not write data files: %s", exc)

            next_run += INTERVAL
            if next_run < time.monotonic():
                next_run = time.monotonic()  # too late: skip missed rounds
            time.sleep(max(0, next_run - time.monotonic()))

        self.store.flush_summary()
        log.info("collector stopped")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    data_dir = os.environ.get("HSM_DATA_DIR", "data")
    Collector(data_dir).run()


if __name__ == "__main__":
    main()
