"""Read the current state of all LXD instances.

This is the only module in the collector that talks to LXD.
It returns plain dictionaries, so the rest of the collector
does not depend on pylxd objects.
"""
import time

import pylxd


def connect():
    """Connect to the local LXD daemon through its unix socket.

    The timeout (seconds) stops a slow or stuck LXD from freezing
    the collector: the call fails and the main loop retries later.
    """
    return pylxd.Client(timeout=5)


def _ipv4(network):
    """Return the first global IPv4 address, ignoring loopback."""
    for nic_name, nic in network.items():
        if nic_name == "lo":
            continue
        for addr in nic.get("addresses") or []:
            if addr.get("family") == "inet" and addr.get("scope") == "global":
                return addr.get("address")
    return None


def _net_totals(network):
    """Add up received/sent bytes on all interfaces except loopback."""
    rx = tx = 0
    for nic_name, nic in network.items():
        if nic_name == "lo":
            continue
        counters = nic.get("counters") or {}
        rx += counters.get("bytes_received", 0)
        tx += counters.get("bytes_sent", 0)
    return rx, tx


def read_all(client):
    """Return one dictionary per instance with raw counters and facts."""
    readings = []
    for inst in client.instances.all():
        state = inst.state()
        network = state.network or {}
        memory = state.memory or {}
        root_disk = (state.disk or {}).get("root") or {}
        rx, tx = _net_totals(network)
        image = "{} {}".format(
            inst.config.get("image.os", ""),
            inst.config.get("image.version", ""),
        ).strip()

        readings.append({
            "id": inst.config.get("volatile.uuid", inst.name),
            "name": inst.name,
            "status": state.status,
            "taken_at": time.monotonic(),
            "cpu_ns": (state.cpu or {}).get("usage", 0),
            "mem_used": memory.get("usage", 0),
            "mem_total": memory.get("total", 0),
            "disk_used": root_disk.get("usage", 0),
            "disk_total": root_disk.get("total", 0),
            "rx_bytes": rx,
            "tx_bytes": tx,
            "processes": state.processes,
            "ipv4": _ipv4(network),
            "image": image,
            "last_used_at": inst.last_used_at,
        })
    return readings
