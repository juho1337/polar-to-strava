"""``polar-to-strava-engine``: JSON Lines command engine for the desktop application.

One JSON request object is read from the first line of stdin; later stdin lines are control
messages (``{"type": "cancel"}``). Every stdout line is a versioned envelope (see
``integration.contract``); stderr carries no secrets and is for humans only.
"""

from __future__ import annotations

import json
import multiprocessing
import platform
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import IO, Any

import httpx
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from core.errors import (
    AuditCancelledError,
    ConfigurationError,
    PolarToStravaError,
)
from core.errors import ValidationError as DomainValidationError
from integration import commands_audit, commands_migration, commands_strava
from integration.context import Context
from integration.contract import (
    CONTRACT_VERSION,
    EXIT_CANCELLED,
    EXIT_FAILURE,
    EXIT_INVALID_REQUEST,
    EXIT_LOCKED,
    EXIT_OK,
    Emitter,
    EngineError,
)
from integration.lock import WorkspaceLockedError
from integration.requests import (
    ActivitiesListRequest,
    ActivityGetRequest,
    ActivityListRequest,
    AuditRequest,
    AuthorizeBeginRequest,
    AuthorizeCompleteRequest,
    ConnectionRequest,
    ReportExportRequest,
    Request,
    ResetRequest,
    StatusRequest,
    TimezoneApplyRequest,
    TimezoneListRequest,
    TimezonePreviewRequest,
    UploadDryRunRequest,
    UploadRunRequest,
)

Handler = Callable[[Any, Context], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Command:
    model: type[BaseModel]
    handler: Handler


def _version(request: Request, context: Context) -> dict[str, Any]:
    try:
        engine = metadata.version("polar-to-strava")
    except metadata.PackageNotFoundError:
        engine = "unknown"
    try:
        import zoneinfo

        zoneinfo.ZoneInfo("Europe/Helsinki")
        iana = True
    except Exception:
        iana = False
    return {
        "contract": CONTRACT_VERSION,
        "engine": engine,
        "python": platform.python_version(),
        "commands": sorted(COMMANDS),
        "capabilities": {
            "incremental_audit": True,
            "cancellation": True,
            "dpapi_token_store": sys.platform == "win32",
            "timezone_iana": iana,
            "activity_lookup": True,
        },
    }


COMMANDS: dict[str, Command] = {
    "version": Command(Request, _version),
    "audit": Command(AuditRequest, commands_audit.audit),
    "audit.dry_run": Command(AuditRequest, commands_audit.audit_dry_run),
    "status": Command(StatusRequest, commands_migration.status),
    "upload.dry_run": Command(UploadDryRunRequest, commands_migration.upload_dry_run),
    "upload.run": Command(UploadRunRequest, commands_migration.upload_run),
    "activity.list": Command(ActivityListRequest, commands_migration.activity_list),
    "activity.get": Command(ActivityGetRequest, commands_migration.activity_get),
    "reset": Command(ResetRequest, commands_migration.reset),
    "report.export": Command(ReportExportRequest, commands_migration.report_export),
    "strava.connection": Command(ConnectionRequest, commands_strava.connection),
    "strava.authorize.begin": Command(AuthorizeBeginRequest, commands_strava.authorize_begin),
    "strava.authorize.complete": Command(
        AuthorizeCompleteRequest, commands_strava.authorize_complete
    ),
    "strava.disconnect": Command(ConnectionRequest, commands_strava.disconnect),
    "strava.activities.list": Command(ActivitiesListRequest, commands_strava.activities_list),
    "timezone.list_unresolved": Command(TimezoneListRequest, commands_audit.timezone_list),
    "timezone.preview": Command(TimezonePreviewRequest, commands_audit.timezone_preview),
    "timezone.apply": Command(TimezoneApplyRequest, commands_audit.timezone_apply),
}


def _collect_secrets(raw: dict[str, Any]) -> list[str]:
    secrets: list[str] = []
    credentials = raw.get("credentials")
    if isinstance(credentials, dict) and isinstance(credentials.get("client_secret"), str):
        secrets.append(credentials["client_secret"])
    if isinstance(raw.get("code"), str):
        secrets.append(raw["code"])
    return secrets


def _validation_error(error: PydanticValidationError) -> EngineError:
    # Only locations, messages and types: never the offending input values (may be secrets).
    problems = [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors()
    ]
    first = problems[0]["field"] if problems else None
    return EngineError(
        "invalid_request",
        "The request document is invalid.",
        field=first,
        details={"errors": problems},
        exit_code=EXIT_INVALID_REQUEST,
    )


def _failed_count(command: str, summary: dict[str, Any]) -> int:
    if command == "audit":
        return int(summary.get("failed", 0))
    if command == "timezone.apply":
        return int(summary.get("audit", {}).get("failed", 0))
    return 0


def run_command(
    command: str,
    raw_request: dict[str, Any],
    stdout: IO[str],
    *,
    cancel: threading.Event | None = None,
    http: httpx.Client | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Execute one command; always ends with exactly one ``result`` or ``error`` envelope."""
    emitter = Emitter(stdout, command, uuid.uuid4().hex[:12], _collect_secrets(raw_request))
    context = Context(emit=emitter, cancel=cancel or threading.Event(), http=http, sleep=sleep)
    if clock is not None:
        context.clock = clock
    entry = COMMANDS.get(command)
    try:
        if entry is None:
            raise EngineError(
                "unknown_command",
                "Unknown command.",
                field="command",
                details={"commands": sorted(COMMANDS)},
                exit_code=EXIT_INVALID_REQUEST,
            )
        try:
            request = entry.model.model_validate(raw_request)
        except PydanticValidationError as error:
            raise _validation_error(error) from None
        workspace = getattr(request, "workspace", None)
        if workspace is not None and command not in {"audit", "audit.dry_run", "timezone.apply"}:
            if not workspace.is_dir():
                raise EngineError(
                    "workspace_not_found", "Workspace directory does not exist.", field="workspace"
                )
        summary = entry.handler(request, context)
        status = str(summary.pop("status", "completed"))
        if status == "completed" and _failed_count(command, summary):
            status = "completed_with_failures"
        emitter.result(status, summary)
        if status == "cancelled":
            return EXIT_CANCELLED
        return EXIT_FAILURE if status == "completed_with_failures" else EXIT_OK
    except EngineError as error:
        emitter.error(error)
        return error.exit_code
    except WorkspaceLockedError as error:
        emitter.error(
            EngineError(
                "workspace_locked",
                str(error),
                details={"holder_started_at": error.holder_started_at},
                retryable=True,
                exit_code=EXIT_LOCKED,
            )
        )
        return EXIT_LOCKED
    except AuditCancelledError:
        emitter.result("cancelled", {"notice": "cancelled_before_reports_were_written"})
        return EXIT_CANCELLED
    except ConfigurationError as error:
        emitter.error(EngineError("configuration_error", str(error)))
        return EXIT_FAILURE
    except DomainValidationError as error:
        emitter.error(EngineError("validation_error", str(error)))
        return EXIT_FAILURE
    except PolarToStravaError as error:
        emitter.error(EngineError("domain_error", str(error)))
        return EXIT_FAILURE
    except Exception as error:  # never leak tracebacks or values; the type name is enough
        emitter.error(EngineError("internal_error", f"Unexpected {type(error).__name__}."))
        return EXIT_FAILURE


def _watch_stdin(stream: IO[str], cancel: threading.Event, cancel_on_close: bool) -> None:
    for line in stream:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("type") == "cancel":
            cancel.set()
            return
    if cancel_on_close:
        cancel.set()


def main(
    argv: list[str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    multiprocessing.freeze_support()  # required for audit worker processes in a frozen Windows exe
    args = list(sys.argv[1:] if argv is None else argv)
    if stdin is None:
        stdin = sys.stdin
        if hasattr(stdin, "reconfigure"):
            stdin.reconfigure(encoding="utf-8")  # requests are UTF-8 whatever the locale
    out = stdout or sys.stdout
    if stdout is None and hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8", newline="\n")
    if not args or args[0] in {"-h", "--help"}:
        print("usage: polar-to-strava-engine <command>   (request JSON on stdin)", file=sys.stderr)
        print("commands: " + ", ".join(sorted(COMMANDS)), file=sys.stderr)
        return EXIT_OK if args else EXIT_INVALID_REQUEST
    command = args[0]
    first = stdin.readline().lstrip("﻿")  # hosts and shells may prepend a UTF-8 BOM
    try:
        raw = json.loads(first) if first.strip() else {}
        if not isinstance(raw, dict):
            raise ValueError("request must be an object")
    except ValueError:
        Emitter(out, command, uuid.uuid4().hex[:12]).error(
            EngineError(
                "invalid_request",
                "The first stdin line must be one JSON object.",
                exit_code=EXIT_INVALID_REQUEST,
            )
        )
        return EXIT_INVALID_REQUEST
    cancel = threading.Event()
    threading.Thread(
        target=_watch_stdin,
        args=(stdin, cancel, bool(raw.get("cancel_on_stdin_close"))),
        daemon=True,
    ).start()
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: cancel.set())
    return run_command(command, raw, out, cancel=cancel)


if __name__ == "__main__":
    sys.exit(main())
