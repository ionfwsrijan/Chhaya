"""The console API behind API Gateway.

Mangum adapts API Gateway's HTTP events to the same ASGI app ``uvicorn``
serves locally, so the console a judge opens on the live URL is byte-for-byte
the one in ``web/index.html`` — there is no second build and no drift between
the demo and the deployment.

The app is built once at import (Lambda reuses the container across warm
invocations) and reads its configuration from the environment the SAM template
sets, exactly as ``make local`` reads it from ``.env``.
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from chhaya.api.app import create_app

__all__ = ["handler"]

# One app for the life of the container; the store and policy load once.
_APP = create_app()
_handler = Mangum(_APP, lifespan="off")


def _strip_stage(event: dict[str, Any]) -> None:
    """Drop the stage prefix HTTP API v2 sends on ``rawPath``.

    Requests to ``https://<api>.../prod/api/health`` arrive with
    ``rawPath="/prod/api/health"``, but FastAPI mounts every route at the
    root. Stripping the leading stage keeps the console URL honest without
    coupling the app itself to a stage name.
    """
    stage = event.get("requestContext", {}).get("stage", "")
    if not stage:
        return
    raw = event.get("rawPath", "")
    prefix = "/" + stage
    if raw == prefix:
        target = "/"
    elif raw.startswith(prefix + "/"):
        target = raw[len(prefix) :]
    else:
        return
    event["rawPath"] = target
    http = event.get("requestContext", {}).get("http", {})
    if isinstance(http, dict) and http.get("path") is not None:
        http["path"] = target


def handler(event: dict[str, Any], context: Any = None) -> Any:
    """Lambda entry point for API Gateway HTTP API (payload v2)."""
    _strip_stage(event)
    return _handler(event, context)
