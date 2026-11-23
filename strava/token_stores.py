"""Alternative token stores for hosts that must not keep OAuth tokens in plaintext."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from core.atomic import atomic_write_bytes
from core.errors import ConfigurationError
from strava.models import TokenSet

# Shared with hosts that read the same file with ProtectedData(CurrentUser, this entropy).
DPAPI_ENTROPY = b"polar-to-strava/strava-tokens/v1"


def _blob_call(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise ConfigurationError("Windows DPAPI token storage is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make(value: bytes) -> tuple[Blob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(value, len(value))
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    source, keep_source = make(data)
    entropy, keep_entropy = make(DPAPI_ENTROPY)
    result = Blob()
    function = (
        ctypes.windll.crypt32.CryptProtectData  # type: ignore[attr-defined,unused-ignore]
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData  # type: ignore[attr-defined,unused-ignore]
    )
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN; the default scope is the current user
    ok = function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(result)
    )
    del keep_source, keep_entropy
    if not ok:
        raise ConfigurationError("Windows DPAPI operation failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)  # type: ignore[attr-defined,unused-ignore]


class DpapiTokenStore:
    """Tokens encrypted with Windows DPAPI for the current user (never plaintext on disk)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TokenSet:
        try:
            plain = _blob_call(self.path.read_bytes(), protect=False)
            return TokenSet.model_validate_json(plain)
        except (OSError, ValidationError, ValueError):
            raise ConfigurationError("Strava authentication is missing or invalid") from None

    def save(self, tokens: TokenSet) -> None:
        atomic_write_bytes(self.path, _blob_call(tokens.model_dump_json().encode(), protect=True))
