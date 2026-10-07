"""Read metric history from the collector's TinyFlux files."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyflux import TagQuery, TimeQuery, TinyFlux

from collector.storage import MEASUREMENT, RAW_NAME, SUMMARY_NAME

FIELDS = ("cpu", "mem", "mem_max", "disk", "disk_max", "rx", "tx", "procs")
MAX_POINTS = 500

RANGES = {
    "15m": (timedelta(minutes=15), "raw"),
    "1h": (timedelta(hours=1), "raw"),
    "6h": (timedelta(hours=6), "summary"),
    "24h": (timedelta(hours=24), "summary"),
    "7d": (timedelta(days=7), "summary"),
    "30d": (timedelta(days=30), "summary"),
}
SOURCES = {
    "raw": (RAW_NAME, timedelta(hours=1)),
    "summary": (SUMMARY_NAME, timedelta(days=1)),
}


def _files(folder, name_format, span, start, end):
    """Yield the existing files that can hold data between start and end."""
    t = start.replace(minute=0, second=0, microsecond=0)
    if span >= timedelta(days=1):
        t = t.replace(hour=0)
    while t <= end:
        path = folder / t.strftime(name_format)
        if path.exists():
            yield path
        t += span


def _average(values, key):
    values = [v for v in values if v is not None]
    if not values:
        return None
    avg = sum(values) / len(values)
    return round(avg, 3) if key == "cpu" else round(avg)


def _to_columns(points):
    """Turn points into columns, averaging groups if there are too many."""
    group = max(1, -(-len(points) // MAX_POINTS))  # ceiling division
    columns = {"t": [], **{key: [] for key in FIELDS}}
    for i in range(0, len(points), group):
        chunk = points[i:i + group]
        columns["t"].append(int(chunk[0].time.timestamp()))
        for key in FIELDS:
            columns[key].append(_average([p.fields.get(key) for p in chunk], key))
    return columns


def query(data_dir, container_id, range_name):
    length, source = RANGES[range_name]
    name_format, span = SOURCES[source]
    end = datetime.now(timezone.utc)
    start = end - length
    q = (TagQuery().id == container_id) & (TimeQuery() >= start)

    points = []
    for path in _files(Path(data_dir) / source, name_format, span, start, end):
        with TinyFlux(str(path), auto_index=False, access_mode="r") as db:
            points.extend(db.search(q, measurement=MEASUREMENT))
    return {"range": range_name, **_to_columns(points)}
