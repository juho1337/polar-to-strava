"""Small mockable client for the official Strava V3 API."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from core.errors import ConfigurationError
from strava.models import RateLimit, StravaTokenResponse, TokenSet, UploadStatus

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
REQUIRED_SCOPE = "activity:write"


class StravaAPIError(Exception):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code


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

    def upload(self, path: Path, external_id: str) -> UploadStatus:
        self._check_budget()
        token = self.access_token()
        try:
            with path.open("rb") as stream:
                response = self.http.post(
                    f"{API_URL}/uploads",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"data_type": "fit", "external_id": external_id},
                    files={"file": (path.name, stream, "application/octet-stream")},
                )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise StravaAPIError(
                "uncertain", "Upload outcome is unknown after network failure"
            ) from error
        return self._upload_response(response, expected=201)

    def get_upload(self, upload_id: str) -> UploadStatus:
        self._check_budget()
        token = self.access_token()
        try:
            response = self.http.get(
                f"{API_URL}/uploads/{upload_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise StravaAPIError("network", "Temporary network failure", retryable=True) from error
        return self._upload_response(response, expected=200)

    def _upload_response(self, response: httpx.Response, expected: int) -> UploadStatus:
        self._capture_rate_limit(response)
        if response.status_code == 429:
            raise StravaAPIError(
                "rate_limit", "Strava rate limit exhausted", retryable=True, status_code=429
            )
        if response.status_code in {401, 403}:
            raise StravaAPIError(
                "authorization", "Strava authorization failed", status_code=response.status_code
            )
        if response.status_code >= 500:
            raise StravaAPIError(
                "server",
                "Temporary Strava server failure",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code != expected:
            raise StravaAPIError(
                "request", "Strava rejected the request", status_code=response.status_code
            )
        try:
            result = UploadStatus.model_validate(response.json())
        except (json.JSONDecodeError, ValueError, ValidationError) as error:
            raise StravaAPIError("malformed_response", "Malformed Strava response") from error
        return result

    def _check_budget(self) -> None:
        if self.rate_limit is not None and self.rate_limit.exhausted:
            raise StravaAPIError("rate_limit", "Strava rate limit budget is exhausted")

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
