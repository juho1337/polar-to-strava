"""Small mockable client for the official Strava V3 API."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from core.errors import ConfigurationError
from strava.artifacts import VerifiedArtifact
from strava.models import RateLimit, StravaTokenResponse, TokenSet
from strava.recovery import Code, Operation, ResponseEvidence, positive_id
from strava.responses import parse_upload_response

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
REQUIRED_SCOPE = "activity:write"


class FailurePhase(StrEnum):
    NOT_SENT = "not_sent"
    POSSIBLY_SENT = "possibly_sent"
    OBSERVATION = "observation"


@dataclass(frozen=True, slots=True)
class PreparedAccess:
    token: str = field(repr=False)
    expires_at: int


class UploadClient(Protocol):
    """Prepared transport boundary for the future guarded orchestration integration."""

    rate_limit: RateLimit | None

    def prepare_access(self) -> PreparedAccess: ...

    def upload(
        self, artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
    ) -> ResponseEvidence: ...

    def get_upload(self, upload_id: str, access: PreparedAccess) -> ResponseEvidence: ...


class RequestFailure(Exception):
    def __init__(
        self,
        operation: Operation,
        phase: FailurePhase,
        code: Code,
        *,
        status_code: int | None = None,
        evidence: ResponseEvidence | None = None,
    ) -> None:
        super().__init__(Code(code).value)
        self.operation, self.phase, self.code = operation, phase, Code(code)
        self.status_code, self.evidence = status_code, evidence or ResponseEvidence()


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TokenSet:
        try:
            return TokenSet.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            raise ConfigurationError(
                f"Strava authentication is missing or invalid; run strava auth ({self.path})"
            ) from None

    def save(self, tokens: TokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(tokens.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def credentials() -> tuple[str, str]:
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ConfigurationError("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET")
    return client_id, client_secret


def authorization_url(
    client_id: str, redirect_uri: str, state: str | None = None
) -> tuple[str, str]:
    nonce = state or secrets.token_urlsafe(24)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": REQUIRED_SCOPE,
            "state": nonce,
        }
    )
    return f"{AUTH_URL}?{query}", nonce


class StravaClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_store: TokenStore,
        http: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_store = token_store
        self.http = http or httpx.Client(timeout=30)
        self.rate_limit: RateLimit | None = None

    def exchange_code(self, code: str, granted_scope: str | None = None) -> TokenSet:
        tokens = self._token_request(
            {"grant_type": "authorization_code", "code": code.strip()},
            fallback_scope=granted_scope,
            persist=False,
        )
        if REQUIRED_SCOPE not in tokens.scope.split():
            raise ConfigurationError(f"Strava did not grant {REQUIRED_SCOPE}")
        self.token_store.save(tokens)
        return tokens

    def access_token(self) -> str:
        tokens = self.token_store.load()
        if tokens.expires_at <= int(time.time()) + 3600:
            tokens = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
                fallback_scope=tokens.scope,
            )
        if REQUIRED_SCOPE not in tokens.scope.split():
            raise ConfigurationError(f"Strava token does not grant {REQUIRED_SCOPE}")
        return tokens.access_token

    def _token_request(
        self,
        fields: dict[str, str],
        fallback_scope: str | None = None,
        *,
        persist: bool = True,
    ) -> TokenSet:
        try:
            response = self.http.post(
                TOKEN_URL,
                data={"client_id": self.client_id, "client_secret": self.client_secret, **fields},
            )
            response.raise_for_status()
            self._capture_rate_limit(response)
            api_response = StravaTokenResponse.model_validate(response.json())
            tokens = api_response.token_set(fallback_scope)
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise ConfigurationError(
                f"Strava OAuth request failed: {type(error).__name__}"
            ) from None
        if persist:
            self.token_store.save(tokens)
        return tokens

    def prepare_access(self) -> PreparedAccess:
        try:
            token = self.access_token()
            return PreparedAccess(token, self.token_store.load().expires_at)
        except (ConfigurationError, OSError):
            raise RequestFailure(
                Operation.SUBMIT, FailurePhase.NOT_SENT, Code.AUTHORIZATION
            ) from None

    def _ready(
        self, access: PreparedAccess, operation: Operation, upload_id: str | None = None
    ) -> None:
        code = None
        if access.expires_at <= int(time.time()) or not access.token:
            code = Code.AUTHORIZATION
        if self.rate_limit is not None and self.rate_limit.exhausted:
            code = Code.RATE_LIMIT
        if code:
            raise RequestFailure(
                operation,
                (
                    FailurePhase.NOT_SENT
                    if operation == Operation.SUBMIT
                    else FailurePhase.OBSERVATION
                ),
                code,
                evidence=ResponseEvidence(upload_id=upload_id, code=code),
            )

    def upload(
        self, artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
    ) -> ResponseEvidence:
        self._ready(access, Operation.SUBMIT)
        if artifact.stream.closed:
            raise RequestFailure(Operation.SUBMIT, FailurePhase.NOT_SENT, Code.INVALID_ARTIFACT)
        try:
            response = self.http.post(
                f"{API_URL}/uploads",
                headers={"Authorization": f"Bearer {access.token}"},
                data={"data_type": "fit", "external_id": external_id},
                files={"file": (artifact.filename, artifact.stream, "application/octet-stream")},
            )
        except (httpx.HTTPError, OSError, ValueError):
            raise RequestFailure(
                Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.NETWORK
            ) from None
        return self._upload_response(response, Operation.SUBMIT)

    def get_upload(self, upload_id: str, access: PreparedAccess) -> ResponseEvidence:
        if positive_id(upload_id) != upload_id:
            raise RequestFailure(
                Operation.OBSERVE, FailurePhase.OBSERVATION, Code.MISSING_UPLOAD_ID
            )
        self._ready(access, Operation.OBSERVE, upload_id)
        try:
            response = self.http.get(
                f"{API_URL}/uploads/{upload_id}",
                headers={"Authorization": f"Bearer {access.token}"},
            )
        except httpx.HTTPError:
            raise RequestFailure(
                Operation.OBSERVE,
                FailurePhase.OBSERVATION,
                Code.NETWORK,
                evidence=ResponseEvidence(upload_id=upload_id, code=Code.NETWORK),
            ) from None
        return self._upload_response(response, Operation.OBSERVE, upload_id)

    def _upload_response(
        self, response: httpx.Response, operation: Operation, expected_upload_id: str | None = None
    ) -> ResponseEvidence:
        self._capture_rate_limit(response)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        evidence = replace(
            parse_upload_response(
                payload, operation=operation, expected_upload_id=expected_upload_id
            ),
            http_status=response.status_code,
        )
        expected = 201 if operation == Operation.SUBMIT else 200
        if response.status_code != expected:
            code = (
                Code.RATE_LIMIT
                if response.status_code == 429
                else (
                    Code.AUTHORIZATION
                    if response.status_code in {401, 403}
                    else Code.SERVER if response.status_code >= 500 else Code.REQUEST
                )
            )
            phase = (
                FailurePhase.POSSIBLY_SENT
                if operation == Operation.SUBMIT
                else FailurePhase.OBSERVATION
            )
            # Unexpected HTTP cannot establish a terminal result or non-submission.
            evidence = ResponseEvidence(
                upload_id=evidence.upload_id,
                conflicting_ids=evidence.conflicting_ids,
                code=Code.ID_CONFLICT if evidence.code == Code.ID_CONFLICT else code,
                http_status=response.status_code,
            )
            raise RequestFailure(
                operation, phase, code, status_code=response.status_code, evidence=evidence
            )
        return evidence

    def _capture_rate_limit(self, response: httpx.Response) -> None:
        parsed = parse_rate_limit(response.headers)
        if parsed is not None:
            self.rate_limit = parsed


def parse_rate_limit(headers: httpx.Headers) -> RateLimit | None:
    try:
        limits = [int(value) for value in headers["X-RateLimit-Limit"].split(",")]
        usage = [int(value) for value in headers["X-RateLimit-Usage"].split(",")]
        if len(limits) != 2 or len(usage) != 2:
            return None
        read_limits = _header_pair(headers, "X-ReadRateLimit-Limit")
        read_usage = _header_pair(headers, "X-ReadRateLimit-Usage")
        return RateLimit(
            short_limit=limits[0],
            daily_limit=limits[1],
            short_usage=usage[0],
            daily_usage=usage[1],
            read_short_limit=read_limits[0] if read_limits else None,
            read_daily_limit=read_limits[1] if read_limits else None,
            read_short_usage=read_usage[0] if read_usage else None,
            read_daily_usage=read_usage[1] if read_usage else None,
        )
    except (KeyError, ValueError):
        return None


def _header_pair(headers: httpx.Headers, name: str) -> tuple[int, int] | None:
    try:
        values = tuple(int(value) for value in headers[name].split(","))
        return values if len(values) == 2 else None
    except (KeyError, ValueError):
        return None
