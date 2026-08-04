"""Where this agent runner is running — hostname, address, region, cloud/local.

Advertised in the ``Register`` frame so the Workers page can name the machine
behind a runner and tell a deployed instance from a developer's laptop. Purely
descriptive: the orchestrator never selects on these.

Auto-detected, with env overrides for the cases a guess gets wrong (NAT'd
address, an unlabelled region, a self-hosted box that should read as cloud):

* ``UNPOD_WORKER_HOSTNAME``
* ``UNPOD_WORKER_ADDRESS``
* ``UNPOD_WORKER_REGION``
* ``UNPOD_WORKER_DEPLOYMENT`` (``cloud`` | ``local``)

Detection never raises: a runner that cannot describe itself still registers.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Dict

# Presence of any of these in env means a platform is running us.
_CLOUD_MARKERS = (
    "KUBERNETES_SERVICE_HOST",
    "ECS_CONTAINER_METADATA_URI",
    "ECS_CONTAINER_METADATA_URI_V4",
    "AWS_EXECUTION_ENV",
    "AWS_LAMBDA_FUNCTION_NAME",
    "K_SERVICE",
    "GAE_ENV",
    "WEBSITE_INSTANCE_ID",
    "FLY_APP_NAME",
    "RAILWAY_ENVIRONMENT",
    "RENDER",
    "MODAL_TASK_ID",
    "CEREBRIUM_DEPLOYMENT_ID",
    "BASETEN_DEPLOYMENT_ID",
)

_REGION_ENVS = (
    "UNPOD_WORKER_REGION",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "GOOGLE_CLOUD_REGION",
    "CLOUD_RUN_REGION",
    "FLY_REGION",
    "RAILWAY_REGION",
    "REGION",
)


def detect_hostname() -> str | None:
    """This machine's name (``UNPOD_WORKER_HOSTNAME`` wins)."""
    override = os.environ.get("UNPOD_WORKER_HOSTNAME", "").strip()
    if override:
        return override
    try:
        return socket.gethostname() or None
    except OSError:
        return None


def detect_address() -> str | None:
    """The IP this box would use to reach the network.

    Connects a UDP socket to a public address and reads the local end — no
    packet leaves, no DNS lookup, nothing blocks. Behind NAT this is the
    private address (the useful one inside a cluster); set
    ``UNPOD_WORKER_ADDRESS`` when you want the public one shown.
    """
    override = os.environ.get("UNPOD_WORKER_ADDRESS", "").strip()
    if override:
        return override
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0]) or None
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname()) or None
        except OSError:
            return None
    finally:
        if sock is not None:
            sock.close()


def detect_region() -> str | None:
    """Cloud region / site label from env, or None when nothing publishes one."""
    for env in _REGION_ENVS:
        value = os.environ.get(env, "").strip()
        if value:
            return value
    return None


def detect_deployment() -> str:
    """``cloud`` when a platform marker is in env, else ``local``.

    An unrecognised ``UNPOD_WORKER_DEPLOYMENT`` is ignored rather than sent, so
    the field stays a two-value enum the dashboard can style on.
    """
    override = os.environ.get("UNPOD_WORKER_DEPLOYMENT", "").strip().lower()
    if override in ("cloud", "local"):
        return override
    return "cloud" if any(os.environ.get(m) for m in _CLOUD_MARKERS) else "local"


def placement() -> Dict[str, Any]:
    """All four placement fields, ready to merge into the capabilities dict."""
    return {
        "hostname": detect_hostname(),
        "address": detect_address(),
        "region": detect_region(),
        "deployment": detect_deployment(),
    }


__all__ = [
    "detect_address",
    "detect_deployment",
    "detect_hostname",
    "detect_region",
    "placement",
]
