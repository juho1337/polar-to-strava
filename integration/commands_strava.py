"""Strava connection and OAuth helper commands. Tokens and secrets are never emitted."""

from __future__ import annotations

import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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
from integration.lock import WorkspaceLock
from integration.requests import (
    ActivitiesListRequest,
    AuthorizeBeginRequest,
    AuthorizeCompleteRequest,
    ConnectionRequest,
)
from strava.activities import ActivityLookupError, list_activities
from strava.client import REQUIRED_SCOPE, ScopeNotGrantedError, authorization_url
from strava.rate_limit import RateLimitPolicy, RateWait

MAX_LOOKUP_DAYS = 400
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
    # Token writes share the workspace lock with uploads, so a refresh in a running upload cannot
    # interleave with authorization (or disconnect) and resurrect or overwrite credentials.
    with WorkspaceLock(request.workspace, "strava.authorize"):
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
            raise EngineError(
                "oauth_failed", "Strava authorization could not be completed."
            ) from None
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


def activities_list(request: ActivitiesListRequest, context: Context) -> dict[str, Any]:
    """Read-only lookup of existing Strava activities for duplicate detection (SPEC-005)."""
    start, end = _utc(request.from_utc), _utc(request.to_utc)
    if end <= start or end - start > timedelta(days=MAX_LOOKUP_DAYS):
        raise EngineError(
            "validation_failed",
            f"The window must be positive and at most {MAX_LOOKUP_DAYS} days.",
            field="to_utc",
        )
    # Token refresh writes the token store, so the lookup shares the workspace lock with uploads.
    with WorkspaceLock(request.workspace, "strava.activities.list"):
        client = make_client(request, context)
        try:

            def waiting(notice: RateWait) -> None:
                context.emit.event(
                    "rate.wait",
                    reason="short_window_reserve",
                    seconds=notice.seconds,
                    resume_at=notice.resume_at.isoformat().replace("+00:00", "Z"),
                )

            policy = RateLimitPolicy(sleep=context.interruptible_sleep, on_wait=waiting)

            def page(number: int, count: int, total: int) -> None:
                context.emit.event("strava.activities.page", page=number, count=count, total=total)

            try:
                result = list_activities(
                    client,
                    start=start,
                    end=end,
                    policy=policy,
                    should_stop=context.cancel.is_set,
                    on_page=page,
                    max_pages=request.max_pages,
                )
            except ActivityLookupError as error:
                raise EngineError(error.code, str(error), details=error.details) from None
        finally:
            if context.http is None:
                client.http.close()
    stopped = (
        {"code": result.stop_code, "message": result.stop_message} if result.stop_code else None
    )
    summary = {
        "complete": result.complete,
        "covered_from": _stamp(start) if result.complete else None,
        "covered_to": _stamp(end) if result.complete else None,
        "pages": result.pages,
        "limited_visibility": result.limited_visibility,
        "stopped": stopped,
        "count": len(result.activities),
        "activities": [asdict(activity) for activity in result.activities],
    }
    if result.stop_code == "cancelled":
        return {"status": "cancelled", **summary}
    return {"status": "completed" if result.complete else "completed_with_failures", **summary}


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def disconnect(request: ConnectionRequest, context: Context) -> dict[str, Any]:
    """Forget tokens and the athlete identity. The recovery history is untouched."""
    with WorkspaceLock(request.workspace, "strava.disconnect"):
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
