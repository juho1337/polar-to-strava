"""Shared command context and Strava wiring helpers (credentials never leave this process)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from core.atomic import atomic_write_text
from core.errors import ConfigurationError
from integration.contract import Emitter, EngineError
from integration.requests import StravaRequest, TokenStoreSpec
from strava.client import StravaClient, TokenStore, TokenStoreLike, credentials
from strava.token_stores import DpapiTokenStore

ATHLETE_FILENAME = ".strava-athlete.json"


@dataclass
class Context:
    emit: Emitter
    cancel: threading.Event = field(default_factory=threading.Event)
    http: httpx.Client | None = None  # injected by tests; production creates its own
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] | None = None

    def interruptible_sleep(self, seconds: float) -> None:
        """Wait, but raise KeyboardInterrupt (the runner's stop path) when cancelled."""
        if self.sleep is not None:
            self.sleep(seconds)
            return
        if self.cancel.wait(seconds):
            raise KeyboardInterrupt


def token_store_from_spec(spec: TokenStoreSpec, workspace: Path) -> TokenStoreLike:
    if spec.kind == "dpapi":
        return DpapiTokenStore(spec.path or workspace / ".strava-tokens.dpapi")
    return TokenStore(spec.path or workspace / ".strava-tokens.json")


def token_store_for(request: StravaRequest) -> TokenStoreLike:
    return token_store_from_spec(request.token_store, request.workspace)


def resolve_credentials(request: StravaRequest, context: Context) -> tuple[str, str]:
    """Credentials from the request (preferred) or the CLI-compatible environment."""
    if request.credentials is not None:
        secret = request.credentials.client_secret.get_secret_value()
        context.emit.register_secret(secret)
        return request.credentials.client_id, secret
    try:
        client_id, secret = credentials()
    except ConfigurationError:
        raise EngineError(
            "credentials_missing",
            "Strava client credentials were not provided.",
            field="credentials",
        ) from None
    context.emit.register_secret(secret)
    return client_id, secret


def make_client(request: StravaRequest, context: Context) -> StravaClient:
    client_id, secret = resolve_credentials(request, context)
    return StravaClient(client_id, secret, token_store_for(request), http=context.http)


def read_athlete(workspace: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((workspace / ATHLETE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_athlete(workspace: Path, athlete: dict[str, Any] | None) -> None:
    if athlete is not None:
        atomic_write_text(workspace / ATHLETE_FILENAME, json.dumps(athlete))
