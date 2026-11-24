"""Strava connection and OAuth helper commands. Tokens and secrets are never emitted."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from core.errors import ConfigurationError
from integration.context import (
    Context,
    make_client,
    read_athlete,
    resolve_credentials,
    token_store_for,
    write_athlete,
)
from integration.contract import EngineError
from integration.requests import (
    AuthorizeBeginRequest,
    AuthorizeCompleteRequest,
    ConnectionRequest,
)
from strava.client import REQUIRED_SCOPE, ScopeNotGrantedError, authorization_url

SCOPES_FOR_FEATURE = {"migration": (REQUIRED_SCOPE,), "sync": ("activity:read_all",)}
READ_SCOPES = {"read", "activity:read", "activity:read_all"}


def _scopes(features: list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for feature in features:
        for scope in SCOPES_FOR_FEATURE[feature]:
            if scope not in ordered:
                ordered.append(scope)
    return tuple(ordered)


def _capabilities(scopes: list[str]) -> dict[str, bool]:
    return {
        "migration_ready": REQUIRED_SCOPE in scopes,
        "sync_ready": bool(READ_SCOPES & set(scopes)),
    }


def connection(request: ConnectionRequest, context: Context) -> dict[str, Any]:
    """Local connection state; makes no network request."""
    store = token_store_for(request)
    try:
        tokens = store.load()
    except ConfigurationError:
        return {
            "connected": False,
            "scopes": [],
            "athlete": read_athlete(request.workspace),
            "expires_at": None,
            "expired": None,
            **_capabilities([]),
        }
    scopes = sorted(set(tokens.scope.split()))
    expires = datetime.fromtimestamp(tokens.expires_at, UTC)
    return {
        "connected": True,
        "scopes": scopes,
        "athlete": read_athlete(request.workspace),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "expired": tokens.expires_at <= int(context.clock()),
        **_capabilities(scopes),
    }


def _validate_redirect(uri: str) -> None:
    parts = urlsplit(uri)
    if parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1"}:
        raise EngineError(
            "invalid_redirect_uri",
            "The redirect URI must be an http loopback address (localhost or 127.0.0.1).",
            field="redirect_uri",
        )


def authorize_begin(request: AuthorizeBeginRequest, context: Context) -> dict[str, Any]:
    _validate_redirect(request.redirect_uri)
    client_id, _ = resolve_credentials(request, context)
    scopes = _scopes(list(request.features))
    nonce = request.state or secrets.token_urlsafe(24)
    url, nonce = authorization_url(client_id, request.redirect_uri, nonce, scopes)
    # The host opens the browser, runs the loopback listener and verifies the nonce.
    return {
        "authorize_url": url,
        "nonce": nonce,
        "scopes": list(scopes),
        "redirect_uri": request.redirect_uri,
    }


def authorize_complete(request: AuthorizeCompleteRequest, context: Context) -> dict[str, Any]:
    code = request.code.get_secret_value()
    context.emit.register_secret(code)
    returned = request.state.get_secret_value()
    if not secrets.compare_digest(
        returned.encode(), request.expected_state.get_secret_value().encode()
    ):
        raise EngineError(
            "state_mismatch",
            "The OAuth state parameter does not match; authorization was not completed.",
            field="state",
        )
    client = make_client(request, context)
    required = _scopes(list(request.features))
    try:
        tokens = client.exchange_code(code, request.granted_scope, required)
    except ScopeNotGrantedError as error:
        raise EngineError(
            "insufficient_scope",
            "Strava did not grant every requested permission.",
            field="granted_scope",
            details={
                "required": list(required),
                "missing": list(error.missing),
                "granted": sorted(error.granted),
            },
        ) from None
    except ConfigurationError:
        raise EngineError("oauth_failed", "Strava authorization could not be completed.") from None
    finally:
        if context.http is None:
            client.http.close()
    write_athlete(request.workspace, client.last_athlete)
    scopes = sorted(set(tokens.scope.split()))
    return {
        "connected": True,
        "scopes": scopes,
        "athlete": client.last_athlete,
        **_capabilities(scopes),
    }


def disconnect(request: ConnectionRequest, context: Context) -> dict[str, Any]:
    """Forget tokens and the athlete identity. The recovery history is untouched."""
    store = token_store_for(request)
    path = getattr(store, "path", None)
    removed = False
    if path is not None and path.exists():
        path.unlink()
        removed = True
    athlete = request.workspace / ".strava-athlete.json"
    if athlete.exists():
        athlete.unlink()
    return {"disconnected": True, "tokens_removed": removed, "recovery_history": "preserved"}
