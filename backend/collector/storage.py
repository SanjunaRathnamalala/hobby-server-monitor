"""Store metric points in TinyFlux files and keep storage bounded.

Raw points (every round) go into one file per UTC hour.
5-minute averages go into one file per UTC day.
Old data is removed by deleting whole files, which is much cheaper
than removing rows from inside one big file.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyflux import Point, TinyFlux

MEASUREMENT = "ct"
BUCKET_SECONDS = 300  # 5 minutes
RAW_NAME = "%Y-%m-%dT%H.csv"
SUMMARY_NAME = "%Y-%m-%d.csv"


def _bucket_start(t):
    """Start time of the 5-minute block that contains t."""
    seconds = int(t.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _round(key, value):
    """CPU keeps 3 decimals, every other field is a whole number."""
    return round(value, 3) if key == "cpu" else round(value)


def _append(path, points):
    """Append points to a TinyFlux file without building an index."""
    with TinyFlux(str(path), auto_index=False) as db:
        db.insert_multiple(points, compact_key_prefixes=True)


def _delete_older(folder, name_format, span, cutoff):
    """Delete our files whose data all ends before cutoff."""
    for path in folder.glob("*.csv"):
        try:
            started = datetime.strptime(path.name, name_format)
        except ValueError:
            continue  # not a file we created: leave it alone
        started = started.replace(tzinfo=timezone.utc)
        if started + span < cutoff:
            path.unlink()


class MetricStore:
    def __init__(self, data_dir, raw_keep_hours=24, summary_keep_days=30):
        self.raw_dir = Path(data_dir) / "raw"
        self.summary_dir = Path(data_dir) / "summary"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        self.raw_keep = timedelta(hours=raw_keep_hours)
        self.summary_keep = timedelta(days=summary_keep_days)
        self._bucket = None
        self._totals = {}  # container id -> {field: [sum, count]}

    def write(self, now, rows):
        """Store one round.

        now: timezone-aware UTC time of the round.
        rows: {container id: fields dict from metrics.compute}
        """
        bucket = _bucket_start(now)
        if self._bucket is not None and bucket != self._bucket:
            self.flush_summary()
        self._bucket = bucket

        for cid, fields in rows.items():
            per_field = self._totals.setdefault(cid, {})
            for key, value in fields.items():
                total = per_field.setdefault(key, [0, 0])
                total[0] += value
                total[1] += 1

        points = [
            Point(time=now, measurement=MEASUREMENT,
                  tags={"id": cid}, fields=fields)
            for cid, fields in rows.items()
        ]
        if points:
            _append(self.raw_dir / now.strftime(RAW_NAME), points)

    def flush_summary(self):
        """Write the averages of the current 5-minute block, then reset."""
        if self._bucket is None or not self._totals:
            return
        points = []
        for cid, per_field in self._totals.items():
            fields = {key: _round(key, s / c) for key, (s, c) in per_field.items()}
            points.append(Point(time=self._bucket, measurement=MEASUREMENT,
                                tags={"id": cid}, fields=fields))
        _append(self.summary_dir / self._bucket.strftime(SUMMARY_NAME), points)
        self._totals = {}

    def delete_old_files(self, now):
        """Remove raw and summary files that are past their keep time."""
        _delete_older(self.raw_dir, RAW_NAME, timedelta(hours=1),
                      now - self.raw_keep)
        _delete_older(self.summary_dir, SUMMARY_NAME, timedelta(days=1),
                      now - self.summary_keep)
