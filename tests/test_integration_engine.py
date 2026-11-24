"""SPEC-004: desktop engine contract, parity, safety, cancellation and workflows (offline)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from rich.console import Console
from typer.testing import CliRunner

import core.cli as cli
from core.audit_progress import AuditProgressRenderer
from integration.engine import COMMANDS, main, run_command
from integration.lock import WorkspaceLock
from integration.snapshots import progress_dict
from services.migration import load_migration_config
from strava.client import TokenStore
from strava.models import MigrationManifest, TokenSet
from strava.progress import progress_table, snapshot
from strava.recovery import Remote, ResponseEvidence
from strava.state import UploadStateStore
from strava.token_stores import DpapiTokenStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock
from tests.test_strava_uploader import start_attempt, workspace

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs/contract/contract-1.0.schema.json").read_text(encoding="utf-8"))
SAMPLE = ROOT / "tests" / "samples" / "training-session-sanitized.json"
SENTINEL_SECRET = "SENTINEL-CLIENT-SECRET-91827"
SENTINEL_ACCESS = "SENTINEL-ACCESS-TOKEN-55661"
SENTINEL_REFRESH = "SENTINEL-REFRESH-TOKEN-77342"
SENTINEL_CODE = "SENTINEL-AUTH-CODE-33120"


def call(
    command: str, request: dict[str, Any], **options: Any
) -> tuple[int, list[dict[str, Any]], str]:
    stream = io.StringIO()
    code = run_command(command, request, stream, **options)
    text = stream.getvalue()
    return code, [json.loads(line) for line in text.splitlines()], text


def results(lines: list[dict[str, Any]]) -> dict[str, Any]:
    final = [line for line in lines if line["type"] in {"result", "error"}]
    assert len(final) == 1 and final[0] is lines[-1], "exactly one terminal envelope, last"
    return final[0]


def validate(lines: list[dict[str, Any]]) -> None:
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for index, line in enumerate(lines, 1):
        validator.validate(line)
        assert line["seq"] == index  # strictly increasing, gap free


def write_activity(source: Path, name: str, day: str, *, timezone: bool) -> None:
    payload = SAMPLE.read_text(encoding="utf-8").replace("2025-01-01", day)
    data = json.loads(payload)
    data["name"] = name
    if not timezone:
        data.pop("timeZoneOffset")
        data["exercises"][0].pop("timezoneOffset")
    (source / f"training-session-{day}-{name}.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def audited(tmp_path: Path) -> tuple[Path, Path]:
    """Source with two resolved and two unresolved-timezone activities, already audited."""
    source = tmp_path / "source"
    source.mkdir()
    write_activity(source, "ok", "2025-02-01", timezone=True)
    write_activity(source, "winter", "2025-01-15", timezone=False)
    write_activity(source, "summer", "2025-07-15", timezone=False)
    (source / "training-session-broken.json").write_text("{bad", encoding="utf-8")
    output = tmp_path / "workspace"
    code, lines, _ = call("audit", {"source": str(source), "workspace": str(output), "workers": 1})
    assert code == 1 and results(lines)["status"] == "completed_with_failures"
    return source, output


# --- envelopes, validation and errors ----------------------------------------------------


def test_version_and_every_command_registered() -> None:
    code, lines, _ = call("version", {})
    assert code == 0
    summary = results(lines)["summary"]
    assert summary["contract"] == "1.1" and summary["commands"] == sorted(COMMANDS)
    validate(lines)


def test_invalid_request_reports_fields_without_echoing_values(tmp_path: Path) -> None:
    code, lines, text = call(
        "audit", {"source": "x", "workspace": str(tmp_path), "surprise": SENTINEL_SECRET}
    )
    assert code == 2
    error = results(lines)["error"]
    assert error["code"] == "invalid_request" and error["field"] == "surprise"
    assert SENTINEL_SECRET not in text
    code, lines, _ = call("nope", {})
    assert code == 2 and results(lines)["error"]["code"] == "unknown_command"


def test_main_rejects_non_json_first_line_and_stdout_is_pure_jsonl() -> None:
    out = io.StringIO()
    assert main(["version"], io.StringIO("not json\n"), out) == 2
    assert json.loads(out.getvalue())["error"]["code"] == "invalid_request"
    with_bom = io.StringIO()
    assert main(["version"], io.StringIO("﻿{}\r\n"), with_bom) == 0  # UTF-8 BOM tolerated
    process = subprocess.run(
        [sys.executable, "-m", "integration", "version"],
        input="{}\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    assert [json.loads(line)["type"] for line in process.stdout.splitlines()] == ["result"]


# --- audit -------------------------------------------------------------------------------


def test_audit_events_conform_and_match_report(audited: tuple[Path, Path]) -> None:
    source, output = audited
    code, lines, _ = call(
        "audit", {"source": str(source), "workspace": str(output), "workers": 1, "overwrite": True}
    )
    validate(lines)
    summary = results(lines)["summary"]
    report = json.loads((output / "migration-audit.json").read_text(encoding="utf-8"))
    assert summary["discovered"] == report["summary"]["discovered"] == 4
    assert summary["needs_configuration"] == 2 and summary["failed"] == 3
    assert summary["fit_generated"] == 1 and summary["ready_to_migrate"] == 1
    activities = [line for line in lines if line.get("event") == "audit.activity"]
    assert len(activities) == 4
    assert all({"activity_name", "average_hr", "maximum_hr"} <= a.keys() for a in activities)
    assert {a["stable_activity_id"] for a in activities} == {
        row["stable_activity_id"] for row in report["activities"]
    }
    progress = [line for line in lines if line.get("event") == "audit.progress"]
    assert progress[-1]["completed"] == progress[-1]["total"] == 4
    assert progress[0]["eta_s"] is None  # not reliable yet


def test_audit_dry_run_writes_nothing_and_classifies(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_activity(source, "ok", "2025-02-01", timezone=True)
    write_activity(source, "notz", "2025-02-02", timezone=False)
    code, lines, _ = call(
        "audit.dry_run", {"source": str(source), "workspace": str(tmp_path / "ws")}
    )
    validate(lines)
    summary = results(lines)["summary"]
    assert code == 0 and summary["fit_generation"] == "not_performed"
    assert summary["preview_status_counts"] == {"requires_configuration": 1, "would_convert": 1}
    assert not (tmp_path / "ws").exists()


def test_audit_cancellation_before_start_writes_no_reports(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_activity(source, "ok", "2025-02-01", timezone=True)
    cancel = threading.Event()
    cancel.set()
    code, lines, _ = call(
        "audit", {"source": str(source), "workspace": str(tmp_path / "ws")}, cancel=cancel
    )
    assert code == 130 and results(lines)["status"] == "cancelled"
    assert not (tmp_path / "ws" / "migration-manifest.json").exists()


def test_stdin_cancel_message_sets_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_activity(source, "ok", "2025-02-01", timezone=True)
    request = json.dumps({"source": str(source), "workspace": str(tmp_path / "ws")})
    out = io.StringIO()
    stdin = io.StringIO(request + "\n" + json.dumps({"type": "cancel"}) + "\n")
    code = main(["audit"], stdin, out)
    assert code in (0, 130)  # the race decides; both end with a single terminal envelope
    assert json.loads(out.getvalue().splitlines()[-1])["type"] == "result"


# --- parity with the CLI (AC-13) ---------------------------------------------------------

PROGRESS_LABELS = {
    "Eligible activities": "eligible",
    "Resolved": "resolved",
    "Completed (eligible)": "completed",
    "Duplicates (eligible)": "duplicate",
    "Remaining (eligible)": "remaining",
    "Ready to submit": "ready_to_submit",
    "Observing": "observing",
    "Needs review / blocked": "needs_review",
    "Capacity-consuming remote jobs (workspace)": "capacity_jobs",
    "Duplicate review stops (no capacity)": "duplicate_review_stops",
    "Local blockers": "local_blocked",
    "Retained outside current eligible": "outside_current_eligible",
    "Outside manifest": "outside_manifest",
    "Resolved outside eligible": "outside_resolved",
}
AUDIT_LABELS = {
    "Activities discovered": "discovered",
    "Processed": "processed",
    "FIT valid": "validated",
    "Ready to migrate": "ready_to_migrate",
    "Needs configuration": "needs_configuration",
    "Excluded": "excluded",
    "Failed": "failed",
    "Warnings": "warnings_total",
}


def table_rows(table: Any) -> dict[str, str]:
    labels = [str(cell) for cell in table.columns[0].cells]
    values = [str(cell) for cell in table.columns[1].cells]
    return dict(zip(labels, values, strict=True))


def test_status_parity_every_cli_row_has_a_contract_field(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(attempt, ResponseEvidence(upload_id="77", remote=Remote.PROCESSING))
        manifest = MigrationManifest.load(root)[0]
        shot = snapshot(manifest, store.records())
    rows = table_rows(progress_table(shot))
    structured = progress_dict(shot)
    for label, value in rows.items():
        if label in {"Resolved"}:
            assert value == f"{structured['resolved']} / {structured['eligible']} (" + (
                f"{structured['percent']:.2f}%)"
            )
            continue
        assert label in PROGRESS_LABELS, f"CLI row without contract mapping: {label}"
        assert value == str(structured[PROGRESS_LABELS[label]])
    code, lines, _ = call("status", {"workspace": str(root)})
    summary = results(lines)["summary"]
    for field in PROGRESS_LABELS.values():
        assert summary[field] == structured[field]
    assert summary["evidence"] == "local_only" and summary["semantics"]


def test_audit_summary_parity(audited: tuple[Path, Path]) -> None:
    source, output = audited
    _, lines, _ = call(
        "audit", {"source": str(source), "workspace": str(output), "workers": 1, "overwrite": True}
    )
    summary = results(lines)["summary"]
    report = json.loads((output / "migration-audit.json").read_text(encoding="utf-8"))
    console = Console(record=True, width=100)
    AuditProgressRenderer(console, output).print_summary(report)
    rendered = console.export_text()
    for label, field in AUDIT_LABELS.items():
        line = next(item for item in rendered.splitlines() if item.strip().startswith(label))
        assert line.split()[-1] == str(summary[field]), label
    assert "Workspace:" in rendered and str(output.name) in rendered
    assert summary["workspace"] == str(output)


def test_status_details_cover_cli_detail_lines(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_uncertain(attempt)
    _, lines, _ = call("status", {"workspace": str(root), "details": True})
    detail = results(lines)["summary"]["activities"][0]
    upload = detail["upload"]
    assert upload["state"] == "review" and "uncertain" in upload["submission_evidence"]
    assert detail["date_available"] is True and detail["resolved_utc_start"].startswith(
        "2025-01-01"
    )
    for key in ("remote", "blockers", "error_codes", "reason_codes", "known_upload_ids"):
        assert key in upload
    runner = CliRunner().invoke(cli.app, ["strava", "status", str(root), "--details"])
    assert "Remote:" in runner.output and "Local blockers:" in runner.output


def test_activity_list_filters_and_get(audited: tuple[Path, Path]) -> None:
    _, output = audited
    request = {"workspace": str(output)}
    # status/activity commands need a recovery state; reconciling creates it
    code, lines, _ = call(
        "activity.list", {**request, "migration_status": ["requires_configuration"]}
    )
    summary = results(lines)["summary"]
    assert summary["total"] == 2
    assert all("configure_timezone" in item["next_actions"] for item in summary["activities"])
    code, lines, _ = call("activity.list", {**request, "limit": 1, "offset": 1})
    assert len(results(lines)["summary"]["activities"]) == 1
    identifier = summary["activities"][0]["stable_activity_id"]
    code, lines, _ = call("activity.get", {**request, "activity_id": identifier})
    assert results(lines)["summary"]["activity"]["stable_activity_id"] == identifier
    code, lines, _ = call("activity.get", {**request, "activity_id": "sha256:missing"})
    assert code == 1 and results(lines)["error"]["code"] == "unknown_activity"


# --- upload ------------------------------------------------------------------------------


def save_tokens(root: Path, scope: str = "activity:write") -> None:
    TokenStore(root / ".strava-tokens.json").save(
        TokenSet(
            access_token=SENTINEL_ACCESS,
            refresh_token=SENTINEL_REFRESH,
            expires_at=9999999999,
            scope=scope,
        )
    )


def transport(
    calls: list[tuple[str, str]], *, cancel: threading.Event | None = None
) -> httpx.Client:
    headers = {"X-RateLimit-Limit": "600,30000", "X-RateLimit-Usage": "12,345"}

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            if cancel is not None:
                cancel.set()  # cancellation arrives while the request is in flight
            return httpx.Response(201, json={"id": 77, "status": "processing"}, headers=headers)
        later = {"X-RateLimit-Limit": "600,30000", "X-RateLimit-Usage": "13,346"}
        return httpx.Response(
            200, json={"id": 77, "status": "ready", "activity_id": 99}, headers=later
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def upload_request(root: Path, **extra: Any) -> dict[str, Any]:
    return {
        "workspace": str(root),
        "credentials": {"client_id": "client-1", "client_secret": SENTINEL_SECRET},
        "selector": {"all": True},
        **extra,
    }


def test_upload_dry_run_makes_no_request_and_labels_actions(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    code, lines, _ = call("upload.dry_run", {"workspace": str(root), "selector": {"all": True}})
    summary = results(lines)["summary"]
    assert code == 0 and summary["notice"] == "local_preview_only"
    assert summary["actions"][0]["label"] == "would_submit"
    assert summary["actions"][0]["stable_activity_id"] == identifier


def test_upload_run_with_fake_transport_emits_progress_rate_and_resolves(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    save_tokens(root)
    calls: list[tuple[str, str]] = []
    clock = Clock()
    with transport(calls) as http:
        code, lines, text = call(
            "upload.run", upload_request(root), http=http, clock=clock.time, sleep=clock.sleep
        )
    validate(lines)
    assert code == 0 and calls == [("POST", "/api/v3/uploads"), ("GET", "/api/v3/uploads/77")]
    final = results(lines)
    assert final["status"] == "completed" and final["summary"]["progress"]["completed"] == 1
    snapshots = [line for line in lines if line.get("event") == "rate.snapshot"]
    assert snapshots
    last = snapshots[-1]
    assert last["overall"]["short"] == {"usage": 13, "limit": 600, "remaining": 587, "known": True}
    # No read-specific headers were sent: unknown is explicit, never zero.
    assert last["read"]["short"] == {
        "usage": None,
        "limit": None,
        "remaining": None,
        "known": False,
    }
    assert (
        final["summary"]["rate"]["max_in_flight"] == 3 and final["summary"]["rate"]["reserve"] == 10
    )
    assert any(line.get("event") == "upload.event" for line in lines)
    for secret in (SENTINEL_SECRET, SENTINEL_ACCESS, SENTINEL_REFRESH):
        assert secret not in text


def test_upload_run_never_retries_uncertain_outcomes(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_uncertain(start_attempt(store, uploader, identifier))
    save_tokens(root)
    calls: list[tuple[str, str]] = []
    with transport(calls) as http:
        code, lines, _ = call("upload.run", upload_request(root), http=http)
    assert calls == []  # neither a POST nor a lookup: the uncertain upload needs human review
    summary = results(lines)["summary"]
    assert summary["network"] is False and summary["progress"]["needs_review"] == 1


def test_upload_cancel_mid_request_then_restart_does_not_post_again(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    save_tokens(root)
    calls: list[tuple[str, str]] = []
    cancel = threading.Event()
    clock = Clock()
    with transport(calls, cancel=cancel) as http:
        code, lines, _ = call(
            "upload.run",
            upload_request(root),
            http=http,
            cancel=cancel,
            clock=clock.time,
            sleep=clock.sleep,
        )
    assert code == 130 and results(lines)["status"] == "cancelled"
    assert calls == [("POST", "/api/v3/uploads")]
    assert (
        results(lines)["summary"]["notice"] == "uncertain_outcomes_are_never_retried_automatically"
    )
    with transport(calls) as http:
        code, lines, _ = call(
            "upload.run", upload_request(root), http=http, clock=clock.time, sleep=clock.sleep
        )
    assert code == 0
    assert [c for c in calls if c[0] == "POST"] == [("POST", "/api/v3/uploads")]  # exactly one


def test_upload_requires_write_scope_and_connection(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    code, lines, _ = call("upload.run", upload_request(root))
    assert code == 1 and results(lines)["error"]["code"] == "not_authorized"
    save_tokens(root, scope="activity:read_all")
    code, lines, _ = call("upload.run", upload_request(root))
    error = results(lines)["error"]
    assert error["code"] == "insufficient_scope" and error["details"]["required"] == [
        "activity:write"
    ]


def test_upload_without_credentials_is_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    root, _ = workspace(tmp_path)
    code, lines, _ = call("upload.run", {"workspace": str(root), "selector": {"all": True}})
    assert results(lines)["error"]["code"] == "credentials_missing"


# --- lock --------------------------------------------------------------------------------


def test_disconnect_and_authorization_wait_for_active_writers(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    save_tokens(root)
    with WorkspaceLock(root, "upload.run"):  # an upload may be refreshing tokens right now
        code, lines, _ = call("strava.disconnect", {"workspace": str(root)})
        assert code == 3 and results(lines)["error"]["code"] == "workspace_locked"
        assert (root / ".strava-tokens.json").exists()  # nothing was deleted
        code, lines, _ = call("strava.authorize.complete", oauth_request(root))
        assert code == 3 and results(lines)["error"]["code"] == "workspace_locked"
    code, lines, _ = call("strava.disconnect", {"workspace": str(root)})
    assert code == 0 and not (root / ".strava-tokens.json").exists()


def test_second_writer_is_refused_and_readers_continue(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    with WorkspaceLock(root, "test"):
        code, lines, _ = call("upload.dry_run", {"workspace": str(root), "selector": {"all": True}})
        error = results(lines)["error"]
        assert code == 3 and error["code"] == "workspace_locked" and error["retryable"] is True
        assert error["details"]["holder_started_at"]
        code, lines, _ = call("status", {"workspace": str(root)})
        assert code == 0 and results(lines)["summary"]["reconciled"] is False
        result = CliRunner().invoke(cli.app, ["strava", "upload", str(root), "--dry-run", "--all"])
        assert result.exit_code == 1 and "only one writer" in result.output
    code, _, _ = call("upload.dry_run", {"workspace": str(root), "selector": {"all": True}})
    assert code == 0  # released


# --- Strava connection and OAuth ---------------------------------------------------------


def token_http(scope: str, calls: list[str]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": SENTINEL_ACCESS,
                "refresh_token": SENTINEL_REFRESH,
                "expires_at": 9999999999,
                "expires_in": 21600,
                "scope": scope,
                "athlete": {"id": 42, "firstname": "Test", "lastname": "Runner", "email": "x@y.z"},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def oauth_request(root: Path, **extra: Any) -> dict[str, Any]:
    return {
        "workspace": str(root),
        "credentials": {"client_id": "client-1", "client_secret": SENTINEL_SECRET},
        "code": SENTINEL_CODE,
        "state": "nonce-1",
        "expected_state": "nonce-1",
        "granted_scope": "read,activity:write",
        **extra,
    }


def test_connection_reports_state_scopes_and_never_tokens(tmp_path: Path) -> None:
    code, lines, _ = call("strava.connection", {"workspace": str(tmp_path)})
    assert results(lines)["summary"]["connected"] is False
    save_tokens(tmp_path, scope="read activity:write")
    _, lines, text = call("strava.connection", {"workspace": str(tmp_path)})
    summary = results(lines)["summary"]
    assert summary["connected"] and summary["migration_ready"] and summary["sync_ready"]
    assert summary["scopes"] == ["activity:write", "read"] and SENTINEL_ACCESS not in text


def test_authorize_begin_requests_only_enabled_features_and_loopback(tmp_path: Path) -> None:
    base = {
        "workspace": str(tmp_path),
        "credentials": {"client_id": "client-1", "client_secret": SENTINEL_SECRET},
        "redirect_uri": "http://localhost:8765/callback",
    }
    _, lines, text = call("strava.authorize.begin", {**base, "features": ["migration"]})
    summary = results(lines)["summary"]
    assert (
        summary["scopes"] == ["activity:write"]
        and "scope=activity%3Awrite" in summary["authorize_url"]
    )
    assert summary["nonce"] in summary["authorize_url"] and SENTINEL_SECRET not in text
    _, lines, _ = call("strava.authorize.begin", {**base, "features": ["migration", "sync"]})
    assert results(lines)["summary"]["scopes"] == ["activity:write", "activity:read_all"]
    code, lines, _ = call(
        "strava.authorize.begin", {**base, "redirect_uri": "https://evil.example/callback"}
    )
    assert results(lines)["error"]["code"] == "invalid_redirect_uri"


def test_authorize_complete_verifies_state_scope_and_keeps_recovery_history(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        start_attempt(store, uploader, identifier)
        before = store.records()
    calls: list[str] = []
    with token_http("read,activity:write", calls) as http:
        code, lines, text = call(
            "strava.authorize.complete", oauth_request(root, state="forged"), http=http
        )
    assert results(lines)["error"]["code"] == "state_mismatch" and calls == []
    with token_http("read", calls) as http:
        code, lines, _ = call("strava.authorize.complete", oauth_request(root), http=http)
    error = results(lines)["error"]
    assert error["code"] == "insufficient_scope" and error["details"]["missing"] == [
        "activity:write"
    ]
    assert not (root / ".strava-tokens.json").exists()
    with token_http("read,activity:write", calls) as http:
        code, lines, text = call("strava.authorize.complete", oauth_request(root), http=http)
    summary = results(lines)["summary"]
    assert code == 0 and summary["connected"] and summary["migration_ready"]
    assert summary["athlete"] == {"id": 42, "firstname": "Test", "lastname": "Runner"}
    for secret in (SENTINEL_SECRET, SENTINEL_ACCESS, SENTINEL_REFRESH, SENTINEL_CODE, "x@y.z"):
        assert secret not in text
    assert TokenStore(root / ".strava-tokens.json").load().access_token == SENTINEL_ACCESS
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        assert store.records() == before  # reauthorization never touches recovery history
    _, lines, _ = call("strava.disconnect", {"workspace": str(root)})
    assert not (root / ".strava-tokens.json").exists()
    assert (root / "migration-state.sqlite3").exists()
    assert results(lines)["summary"]["recovery_history"] == "preserved"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_token_store_is_encrypted_and_roundtrips(tmp_path: Path) -> None:
    store = DpapiTokenStore(tmp_path / "tokens.dpapi")
    tokens = TokenSet(
        access_token=SENTINEL_ACCESS, refresh_token=SENTINEL_REFRESH, expires_at=1, scope="x"
    )
    store.save(tokens)
    assert SENTINEL_ACCESS.encode() not in (tmp_path / "tokens.dpapi").read_bytes()
    assert store.load() == tokens
    code, lines, _ = call(
        "strava.connection",
        {"workspace": str(tmp_path), "token_store": {"kind": "dpapi", "path": str(store.path)}},
    )
    assert results(lines)["summary"]["connected"] is True


# --- timezone workflow -------------------------------------------------------------------


def test_timezone_list_filters_and_preview_uses_historical_dst(audited: tuple[Path, Path]) -> None:
    _, output = audited
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    items = results(lines)["summary"]["activities"]
    assert [item["date"] for item in items] == ["2025-01-15", "2025-07-15"] or {
        item["date"] for item in items
    } == {"2025-01-15", "2025-07-15"}
    assert all(item["original_timezone"]["source_offset_minutes"] is None for item in items)
    assert all(item["reason_code"] == "no_timezone_offset" for item in items)
    _, lines, _ = call(
        "timezone.list_unresolved", {"workspace": str(output), "from_date": "2025-06-01"}
    )
    assert [i["date"] for i in results(lines)["summary"]["activities"]] == ["2025-07-15"]
    by_date = {item["date"]: item["stable_activity_id"] for item in items}
    _, lines, _ = call(
        "timezone.preview",
        {
            "workspace": str(output),
            "iana_zone": "Europe/Helsinki",
            "choices": [{"stable_activity_id": value} for value in by_date.values()],
        },
    )
    previews = {p["stable_activity_id"]: p for p in results(lines)["summary"]["items"]}
    winter, summer = previews[by_date["2025-01-15"]], previews[by_date["2025-07-15"]]
    assert (winter["offset"], summer["offset"]) == ("+02:00", "+03:00")
    assert winter["resulting_utc"].startswith("2025-01-15T08:00:00")
    assert summer["resulting_utc"].startswith("2025-07-15T07:00:00")
    assert winter["warnings"][0]["code"] == "suggestion_requires_confirmation"


def test_timezone_preview_errors_are_structured(audited: tuple[Path, Path]) -> None:
    from datetime import datetime

    from integration.commands_audit import _iana_offset, _preview_one

    _, output = audited
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    identifier = results(lines)["summary"]["activities"][0]["stable_activity_id"]
    choices = [
        {"stable_activity_id": identifier, "offset": "+15:00"},
        {"stable_activity_id": identifier, "iana_zone": "Mars/Olympus"},
        {"stable_activity_id": "sha256:unknown", "offset": "+02:00"},
        {"stable_activity_id": identifier},
    ]
    _, lines, _ = call("timezone.preview", {"workspace": str(output), "choices": choices})
    codes = [p["errors"][0]["code"] for p in results(lines)["summary"]["items"]]
    assert codes == ["invalid_offset", "unknown_timezone", "not_unresolved", "no_offset"]
    # Daylight-saving transitions are never guessed.
    assert (_iana_offset(datetime(2025, 3, 30, 3, 30), "Europe/Helsinki")[1] or {})[
        "code"
    ] == "nonexistent_local_time"
    assert (_iana_offset(datetime(2025, 10, 26, 3, 30), "Europe/Helsinki")[1] or {})[
        "code"
    ] == "ambiguous_local_time"
    assert _iana_offset(datetime(2025, 10, 26, 12, 0), "Europe/Helsinki")[0] == "+02:00"
    # Authoritative source timezone information can never be overridden by a suggestion.
    unresolved = {
        "x": {
            "local_start": "2025-01-15T10:00:00",
            "original_timezone": {"source_offset_minutes": 120, "timestamp_has_zone": False},
        }
    }
    from integration.requests import TimezoneChoice

    result = _preview_one(TimezoneChoice(stable_activity_id="x", offset="+03:00"), None, unresolved)
    assert result["errors"][0]["code"] == "authoritative_timezone"


def test_timezone_apply_requires_confirmation_then_reruns_audit(audited: tuple[Path, Path]) -> None:
    source, output = audited
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    items = results(lines)["summary"]["activities"]
    ids = [item["stable_activity_id"] for item in items]
    request = {
        "workspace": str(output),
        "source": str(source),
        "iana_zone": "Europe/Helsinki",
        "choices": [{"stable_activity_id": value} for value in ids],
        "workers": 1,
    }
    code, lines, _ = call("timezone.apply", request)
    assert results(lines)["error"]["code"] == "confirmation_required"
    assert not (output / "migration-config.json").exists()
    bad = {
        **request,
        "confirm": True,
        "choices": [{"stable_activity_id": ids[0], "offset": "+16:00"}],
    }
    code, lines, _ = call("timezone.apply", bad)
    assert results(lines)["error"]["code"] == "validation_failed"
    assert not (output / "migration-config.json").exists()  # nothing changed on validation failure
    manifest_before = {a.stable_activity_id for a in MigrationManifest.load(output)[0].activities}
    code, lines, _ = call("timezone.apply", {**request, "confirm": True})
    validate(lines)
    final = results(lines)
    summary = final["summary"]
    assert summary["applied"] == 2 and summary["notice"] == "no_upload_was_started_or_retried"
    assert {a["status_before"] for a in summary["activities"]} == {"requires_configuration"}
    assert {a["status_after"] for a in summary["activities"]} <= {
        "eligible",
        "eligible_with_warnings",
    }
    config = load_migration_config(output / "migration-config.json")
    assert {k: v.timezone_offset for k, v in config.activity_overrides.items()} == {
        ids[0]: items and ("+02:00" if "01-15" in items[0]["date"] else "+03:00"),
        ids[1]: "+03:00" if "07-15" in items[1]["date"] else "+02:00",
    }
    manifest_after = MigrationManifest.load(output)[0]
    assert {a.stable_activity_id for a in manifest_after.activities} == manifest_before
    assert not any(
        a.migration.status == "requires_configuration" for a in manifest_after.activities
    )
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    assert results(lines)["summary"]["total"] == 0


def test_timezone_apply_rejects_a_changed_source_and_keeps_the_previous_config(
    audited: tuple[Path, Path], tmp_path: Path
) -> None:
    source, output = audited
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    ids = [i["stable_activity_id"] for i in results(lines)["summary"]["activities"]]
    config = output / "migration-config.json"
    before = config.read_bytes() if config.exists() else None
    other = tmp_path / "other-source"  # a different, empty source directory
    other.mkdir()
    request = {
        "workspace": str(output),
        "source": str(other),
        "iana_zone": "Europe/Helsinki",
        "choices": [{"stable_activity_id": value} for value in ids],
        "workers": 1,
        "confirm": True,
    }
    code, lines, _ = call("timezone.apply", request)
    assert results(lines)["error"]["code"] == "source_mismatch" and code != 0
    assert (config.read_bytes() if config.exists() else None) == before


def test_timezone_apply_holds_the_workspace_lock_through_the_audit(
    audited: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from integration.lock import WorkspaceLock, WorkspaceLockedError
    from services.audit import MigrationAudit

    source, output = audited
    _, lines, _ = call("timezone.list_unresolved", {"workspace": str(output)})
    ids = [i["stable_activity_id"] for i in results(lines)["summary"]["activities"]]
    original = MigrationAudit.run
    probes: list[str] = []

    def probing(self: MigrationAudit, *args: Any, **kwargs: Any) -> Any:
        try:
            with WorkspaceLock(output, "competing-writer"):
                probes.append("acquired")  # another writer could have changed the offsets
        except WorkspaceLockedError:
            probes.append("locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MigrationAudit, "run", probing)
    acquisitions: list[str] = []
    init = WorkspaceLock.__init__

    def recording(self: WorkspaceLock, workspace: Path, command: str, *rest: Any) -> None:
        if command != "competing-writer":
            acquisitions.append(command)
        init(self, workspace, command, *rest)

    monkeypatch.setattr(WorkspaceLock, "__init__", recording)
    request = {
        "workspace": str(output),
        "source": str(source),
        "iana_zone": "Europe/Helsinki",
        "choices": [{"stable_activity_id": value} for value in ids],
        "workers": 1,
        "confirm": True,
    }
    _, lines, _ = call("timezone.apply", request)  # exit 1: the fixture has a failed activity
    assert results(lines)["summary"]["applied"] == 2
    assert probes == ["locked"]
    # One uninterrupted acquisition covers the config write and the audit; releasing and
    # re-acquiring (a second "audit" lock) would leave a window for another writer.
    assert acquisitions == ["timezone.apply"]


# --- report export and secrets -----------------------------------------------------------


@pytest.mark.parametrize("fmt", ["json", "csv", "markdown"])
def test_report_export_contains_no_secrets_or_local_paths(tmp_path: Path, fmt: str) -> None:
    root, identifier = workspace(tmp_path)
    save_tokens(root)
    destination = tmp_path / f"report.{fmt}"
    code, lines, text = call(
        "report.export",
        {"workspace": str(root), "destination": str(destination), "format": fmt},
    )
    assert code == 0 and results(lines)["summary"]["activities"] == 1
    exported = destination.read_text(encoding="utf-8")
    assert identifier in exported
    for forbidden in (SENTINEL_ACCESS, SENTINEL_REFRESH, SENTINEL_SECRET, str(tmp_path)):
        assert forbidden not in exported and forbidden not in text


def test_reset_command_returns_protected_actions(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    code, lines, _ = call("reset", {"workspace": str(root), "activity_id": identifier})
    assert code == 0 and results(lines)["summary"]["actions"][0]["kind"] == "submit"
    code, lines, _ = call(
        "reset", {"workspace": str(root), "activity_id": identifier, "force": True}
    )
    assert code == 2  # --force does not exist in the contract


def test_missing_workspace_and_internal_errors_do_not_leak(tmp_path: Path) -> None:
    code, lines, _ = call("status", {"workspace": str(tmp_path / "missing")})
    assert results(lines)["error"]["code"] == "workspace_not_found"
    code, lines, text = call("status", {"workspace": str(tmp_path)})  # no manifest
    assert code == 1 and results(lines)["error"]["code"] == "validation_error"
