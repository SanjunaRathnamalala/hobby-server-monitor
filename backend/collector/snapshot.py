"""Write the latest state of all containers to one small JSON file.

The API reads this file instead of asking LXD, so the cost does not
grow with the number of open browser tabs.
"""
import json
import os


def write(path, data):
    """Replace the file atomically.

    Readers see either the complete old file or the complete new one,
    never a half-written file.
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)
