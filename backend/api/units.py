"""Parse LXD's size and CPU formats into plain numbers."""
import re

_UNITS = {"": 1, "B": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
          "PB": 10**15, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40,
          "PiB": 2**50}
_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([A-Za-z]*)")


def parse_bytes(value, total=None):
    """'512MiB', '1GB', '1073741824' -> bytes. '50%' needs the total it is a
    percentage of. Returns None when unset or not understood."""
    if not value:
        return None
    value = str(value).strip()
    if value.endswith("%"):
        return int(total * float(value[:-1]) / 100) if total else None
    match = _SIZE.fullmatch(value)
    if not match or match.group(2) not in _UNITS:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def parse_cpu(value):
    """'2' -> 2 cores. '0-1,3' (pinned CPU numbers) -> 3 cores. None if unset."""
    if not value:
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    count = 0
    for part in value.split(","):
        start, _, end = part.partition("-")
        if not start.isdigit() or (end and not end.isdigit()):
            return None
        count += int(end) - int(start) + 1 if end else 1
    return count
