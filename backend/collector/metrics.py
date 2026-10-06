"""Turn two raw readings of one container into values to store.

Only calculations: no LXD, no files, no clock.
This makes the functions easy to test.
"""


def _growth(prev, cur, key):
    """How much a counter grew since the last reading.

    Returns None if it went down, which means the counter was reset
    (for example, the container was restarted).
    """
    grown = cur[key] - prev[key]
    return grown if grown >= 0 else None


def compute(prev, cur):
    """Return the numeric fields for one container.

    prev is the reading from the previous round, or None on the
    first round after the collector starts.
    Values that cannot be calculated are left out.
    """
    fields = {
        "mem": cur["mem_used"],
        "mem_max": cur["mem_total"],
        "disk": cur["disk_used"],
        "disk_max": cur["disk_total"],
        "procs": cur["processes"],
    }

    if prev is None:
        return fields

    seconds = cur["taken_at"] - prev["taken_at"]
    if seconds <= 0:
        return fields

    cpu_ns = _growth(prev, cur, "cpu_ns")
    if cpu_ns is not None:
        fields["cpu"] = round(cpu_ns / 1_000_000_000 / seconds, 3)

    rx = _growth(prev, cur, "rx_bytes")
    if rx is not None:
        fields["rx"] = round(rx / seconds)

    tx = _growth(prev, cur, "tx_bytes")
    if tx is not None:
        fields["tx"] = round(tx / seconds)

    return fields
