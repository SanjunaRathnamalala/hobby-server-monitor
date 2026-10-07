"""LXD access for the API's actions.
Reading metrics is the collector's job; the API only reads its snapshot."""
import logging
from contextlib import contextmanager

import falcon
import pylxd
import requests
from pylxd import exceptions as lxd_exceptions

log = logging.getLogger("api")

STOP_TIMEOUT = 30  # seconds a container gets to shut down cleanly


def client():
    """Connect with a timeout longer than a graceful stop can take."""
    try:
        return pylxd.Client(timeout=STOP_TIMEOUT + 15)
    except lxd_exceptions.ClientConnectionFailed as exc:
        log.warning("LXD not reachable: %s", exc)
        raise falcon.HTTPServiceUnavailable(description="LXD is not reachable.")


def find(lxd, uuid):
    """Return the instance with this volatile.uuid, or answer 404."""
    for inst in lxd.instances.all():
        if inst.config.get("volatile.uuid") == uuid:
            return inst
    raise falcon.HTTPNotFound()


@contextmanager
def errors():
    """Turn LXD failures into clear HTTP errors."""
    try:
        yield
    except lxd_exceptions.LXDAPIException as exc:
        raise falcon.HTTPConflict(description=f"LXD refused: {exc}")
    except requests.RequestException as exc:
        log.warning("LXD request failed: %s", exc)
        raise falcon.HTTPServiceUnavailable(description="LXD did not answer in time.")
