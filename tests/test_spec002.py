"""SPEC-002 synthetic recognition, persistent stops and production composition."""

from io import StringIO
from pathlib import Path

import httpx
import pytest
from rich.console import Console

from strava.client import StravaClient, TokenStore
from strava.models import TokenSet
from strava.progress import RecoveryProgressRenderer, snapshot
from strava.rate_limit import RateLimitPolicy
from strava.recovery import (
    Action,
    Code,
    Operation,
    RecoveryEvent,
    Remote,
    ResponseEvidence,
    classify_actions,
)
from strava.responses import parse_upload_response
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock, Pipeline, engine
from tests.test_strava_uploader import multi_workspace, start_attempt, workspace

IDENTIFIER = "sha256:" + "a" * 64
EXTERNAL = IDENTIFIER + ".fit"


def response(identifier: str = IDENTIFIER, upload: int = 77) -> dict[str, object]:
    external = identifier + ".fit"
    return dict(
        id=upload,
        id_str=str(upload),
        activity_id=None,
        status="There was an error processing your activity.",
        external_id=external,
        error=external + " duplicate of <a href='/activities/99' target='_blank'>PRIVATE_TITLE</a>",
    )


@pytest.mark.parametrize(
    "anchor",
    [
        "<a href='/activities/99' target='_blank'>PRIVATE_TITLE</a>",
        '<a target="_blank" href="/activities/99">different &amp; title 123</a>',
        "<a href='/activities/99'></a>",
        "<a href='/activities/99'>&lt;not-an-element&gt;</a>",
    ],
)
@pytest.mark.parametrize("operation", [Operation.SUBMIT, Operation.OBSERVE])
def test_linked_grammar_positive(anchor: str, operation: Operation) -> None:
    payload = response()
    payload["error"] = EXTERNAL + " duplicate of " + anchor
    evidence = parse_upload_response(
        payload,
        operation=operation,
        expected_upload_id="77" if operation == Operation.OBSERVE else None,
        expected_identifier=EXTERNAL,
    )
    assert evidence.remote == Remote.DUPLICATE
    assert evidence.upload_id == "77" and evidence.duplicate_activity_id == "99"
    assert "PRIVATE_TITLE" not in repr(evidence)


@pytest.mark.parametrize(
    "bad",
    [
        "may be a duplicate of ",
        "not a duplicate of ",
        "duplicate",
        "duplicate of <a href='/athletes/123'>TITLE</a>",
        *[
            f"duplicate of <a href='{p}'>TITLE</a>"
            for p in (
                "/activities/0",
                "/activities/-1",
                "/activities/abc",
                "/activities/099",
                "/activities/99/",
                "/activities/99?x=1",
                "/activities/99#x",
                "//example/activities/99",
                "https://example/activities/99",
                "/activities/%39%39",
            )
        ],
        "duplicate of <a href='/activities/99'>T</a><a href='/activities/100'>T</a>",
        "duplicate of <a href='/activities/99' href='/activities/100'>T</a>",
        "duplicate of <a href='/activities/99' onclick='x'>T</a>",
        "duplicate of <a href='/activities/99'><b>T</b></a>",
        "duplicate of <a href='/activities/99'>T</a> maybe",
        "duplicate of <!--x--><a href='/activities/99'>T</a>",
        "duplicate of <a href=/activities/99>T</a>",
        "duplicate of <a href='/activities/99'>T",
        "duplicate of <a href='/activities/99' target='_self'>T</a>",
    ],
)
def test_linked_grammar_negative(bad: str) -> None:
    payload = response()
    payload["error"] = EXTERNAL + " " + bad
    result = parse_upload_response(
        payload, operation=Operation.OBSERVE, expected_upload_id="77", expected_identifier=EXTERNAL
    )
    assert result.remote != Remote.DUPLICATE and result.duplicate_activity_id is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("external_id", "different.fit"),
        ("external_id", None),
        ("id", 78),
        ("id_str", "78"),
        ("activity_id", 99),
        ("activity_id", "99"),
        ("status", "error"),
        ("status", "processing"),
        ("status", None),
        ("error", 123),
        ("error", "prefix " + str(response()["error"])),
    ],
)
def test_linked_context_and_conflicts(field: str, value: object) -> None:
    payload = response()
    payload[field] = value
    result = parse_upload_response(
        payload, operation=Operation.OBSERVE, expected_upload_id="77", expected_identifier=EXTERNAL
    )
    assert result.remote != Remote.DUPLICATE and result.duplicate_activity_id is None


@pytest.mark.parametrize(
    "context", [None, "sha256:bad.fit", "sha256:" + "A" * 64 + ".fit", "different.fit"]
)
def test_missing_or_invalid_expected_context(context: str | None) -> None:
    result = parse_upload_response(
        response(),
        operation=Operation.OBSERVE,
        expected_upload_id="77",
        expected_identifier=context,
    )
    assert result.code == Code.DUPLICATE_UNRECOGNIZED


@pytest.mark.parametrize("outcome", ["linked", "unknown", "post_linked"])
def test_http_composition_reset_restart(tmp_path: Path, outcome: str) -> None:
    root, identifier = workspace(tmp_path)
    tokens = TokenStore(root / ".strava-tokens.json")
    tokens.save(
        TokenSet(
            access_token="synthetic",
            refresh_token="synthetic",
            expires_at=9999999999,
            scope="activity:write",
        )
    )
    calls: list[str] = []
    events: list[RecoveryEvent] = []
    clock = Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        payload = response(identifier)
        if request.method == "POST" and outcome != "post_linked":
            return httpx.Response(201, json={"id": 77, "status": "processing"})
        if outcome == "unknown":
            payload["error"] = "maybe duplicate of something"
        return httpx.Response(201 if request.method == "POST" else 200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = StravaClient("synthetic", "synthetic", tokens, http)
        for _restart in (False, True):
            with UploadStateStore(root / "migration-state.sqlite3") as store:
                uploader = Uploader(
                    root,
                    store,
                    client,
                    policy=RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
                    clock=clock.time,
                    on_event=events.append,
                )
                uploader.run(uploader.select())
                record = store.load(identifier)
                expected = Action.REVIEW if outcome == "unknown" else Action.RESOLVED
                assert {a.kind for a in classify_actions(record)} == {expected}
                assert record.attempts[0].upload_id == "77"
                assert record.attempts[0].duplicate_activity_id == (
                    None if outcome == "unknown" else "99"
                )
                for force in (False, True):
                    store.reset(identifier, force=force)
                    assert {a.kind for a in classify_actions(store.load(identifier))} == {expected}
                dump = "\n".join(store.connection.iterdump())
                assert "PRIVATE_TITLE" not in dump and "<a href" not in dump
                assert snapshot(uploader.manifest, store.records()).observing == 0
    assert calls == (["POST"] if outcome == "post_linked" else ["POST", "GET"])
    assert clock.now <= 2
    assert events[-1].batch_unattempted == 0
    if outcome == "unknown":
        assert events[-1].review_upload_ids == ("77",)


def test_old_duplicate_evidence_ignores_obsolete_blocker_flags(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(
            attempt,
            parse_upload_response(
                {"id": 77, "status": "processing", "error": "duplicate unclear"},
                operation=Operation.SUBMIT,
            ),
        )
        store.connection.execute("UPDATE recovery_blockers SET active=0,blocks_observation=0")
        store.connection.commit()
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        client = Pipeline(Clock())
        engine(root, store, client).run(uploader.select())
        assert client.requests == []
        assert {a.kind for a in store.reset(identifier, force=True)} == {Action.REVIEW}


@pytest.mark.parametrize("review_stop", [False, True])
def test_five_selected_batch_terminates(tmp_path: Path, review_stop: bool) -> None:
    root, identifiers = multi_workspace(tmp_path, 5)
    clock = Clock()
    client = Pipeline(clock)

    def observe(upload: str) -> ResponseEvidence:
        if int(upload) <= 2:
            return client.complete(upload)
        payload = response(identifiers[int(upload) - 1], int(upload))
        if review_stop:
            payload["error"] = "duplicate unknown"
        return parse_upload_response(
            payload,
            operation=Operation.OBSERVE,
            expected_upload_id=upload,
            expected_identifier=identifiers[int(upload) - 1] + ".fit",
        )

    client.observe = observe
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select(limit=5))
        state = snapshot(uploader.manifest, store.records())
        assert state.resolved == (2 if review_stop else 5)
        assert state.needs_review == (3 if review_stop else 0)
        assert state.observing == 0
    assert len(client.posts) == 5 and len(client.gets) == 5 and clock.now < 10


def test_wait_and_capacity_reporting(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 5)
    clock = Clock()
    client = Pipeline(clock)
    events: list[RecoveryEvent] = []
    client.observe = lambda upload: parse_upload_response(
        {"id": int(upload), "status": "processing", "error": "duplicate unknown"},
        operation=Operation.OBSERVE,
        expected_upload_id=upload,
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(
            root,
            store,
            client,
            policy=RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
            clock=clock.time,
            on_event=events.append,
        )
        uploader.run(uploader.select(limit=5))
        assert len(client.posts) == 3 and len(client.gets) == 3
        waits = [e for e in events if e.next_poll_at is not None]
        assert waits and waits[0].polls_remaining == 60 and waits[0].failures_remaining == 3
        assert events[-1].batch_unattempted == 2 and events[-1].batch_review == 3
        renderer = RecoveryProgressRenderer(uploader.manifest, store.records)
        for event, words in [
            (waits[0], ["next GET", "GET budget remaining", "processing_backoff"]),
            (
                events[-1],
                ["Batch finished", "2 selected submissions unattempted", "retained capacity"],
            ),
        ]:
            renderer.update(event)
            out = StringIO()
            Console(file=out, width=240).print(renderer.render())
            assert all(word in out.getvalue() for word in words)


@pytest.mark.parametrize("http_status", [400, 429, 503])
def test_linked_unexpected_http_cannot_resolve(tmp_path: Path, http_status: int) -> None:
    from strava.client import PreparedAccess, RequestFailure

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(http_status, json=response()))
    ) as http:
        client = StravaClient("synthetic", "synthetic", TokenStore(tmp_path / "unused"), http)
        with pytest.raises(RequestFailure) as caught:
            client.get_upload(
                "77", PreparedAccess("synthetic", 9999999999), expected_identifier=EXTERNAL
            )
        assert caught.value.evidence.upload_id == "77"
        assert caught.value.evidence.duplicate_activity_id is None
        assert caught.value.evidence.remote is None


def test_orphaned_native_attempt_retains_matching_context(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(attempt, ResponseEvidence(upload_id="77", remote=Remote.PROCESSING))
    (root / "fits/activity.fit").unlink()
    import json

    manifest = json.loads((root / "migration-manifest.json").read_text())
    manifest["activities"] = []
    (root / "migration-manifest.json").write_text(json.dumps(manifest))

    class Observing(Pipeline):
        def get_upload(
            self, upload_id: str, access: object, *, expected_identifier: str | None = None
        ) -> ResponseEvidence:
            self.gets.append(upload_id)
            assert expected_identifier == EXTERNAL
            return parse_upload_response(
                response(),
                operation=Operation.OBSERVE,
                expected_upload_id=upload_id,
                expected_identifier=expected_identifier,
            )

    client = Observing(Clock())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.DUPLICATE
        assert Action.RESOLVED in {a.kind for a in uploader.select()}
    assert client.gets == ["77"] and client.posts == []


def test_stopped_duplicate_local_cli_is_network_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import core.cli as cli

    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(
            attempt,
            ResponseEvidence(
                upload_id="77", remote=Remote.PROCESSING, code=Code.DUPLICATE_UNRECOGNIZED
            ),
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Local duplicate review attempted network/auth")

    monkeypatch.setattr(cli, "StravaClient", forbidden)
    monkeypatch.setattr(cli, "credentials", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    for args in (
        ["status", str(root), "--details"],
        ["upload", str(root), "--all", "--dry-run"],
        ["upload", str(root), "--all"],
    ):
        result = CliRunner().invoke(cli.app, ["strava", *args])
        assert result.exit_code == 0, result.output
        assert "automatic observation stopped" in result.output
        actions = result.output.split("Local preview only")[0]
        assert "would_observe" not in actions and "would_submit" not in actions
        assert "upload ID: 77" in actions
    details = CliRunner().invoke(cli.app, ["strava", "status", str(root), "--details"])
    assert "upload ID: 77" in details.output


def test_transient_wait_reports_both_remaining_budgets(tmp_path: Path) -> None:
    from strava.client import FailurePhase, RequestFailure

    root, _ = workspace(tmp_path)
    client = Pipeline(Clock())
    events: list[RecoveryEvent] = []

    def fail(upload: str) -> ResponseEvidence:
        raise RequestFailure(Operation.OBSERVE, FailurePhase.OBSERVATION, Code.NETWORK)

    client.observe = fail
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        runner = engine(root, store, client)
        runner.on_event = events.append
        runner.run(uploader.select())
    waits = [e for e in events if e.wait_reason == "transient_backoff"]
    assert [(e.polls_remaining, e.failures_remaining) for e in waits] == [(59, 2), (58, 1)]
    assert len(client.gets) == 3


def test_legacy_attempt_cannot_invent_linked_identifier_context(tmp_path: Path) -> None:
    from tests.test_strava_state import legacy

    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    legacy(
        path,
        stable_activity_id=identifier,
        status="processing",
        strava_upload_id="77",
        attempt_count=1,
    )

    class Observing(Pipeline):
        def get_upload(
            self, upload_id: str, access: object, *, expected_identifier: str | None = None
        ) -> ResponseEvidence:
            self.gets.append(upload_id)
            assert expected_identifier is None
            return parse_upload_response(
                response(),
                operation=Operation.OBSERVE,
                expected_upload_id=upload_id,
                expected_identifier=expected_identifier,
            )

    client = Observing(Clock())
    for _ in range(2):
        with UploadStateStore(path) as store:
            uploader = Uploader(root, store)
            engine(root, store, client).run(uploader.select())
            assert store.load(identifier).attempts[0].duplicate_activity_id is None
            assert {a.kind for a in uploader.select()} == {Action.REVIEW}
    assert client.gets == ["77"] and client.posts == []


def test_linked_result_write_failure_preserves_get_only_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    client = Pipeline(Clock())
    client.observe = lambda upload: parse_upload_response(
        response(identifier, int(upload)),
        operation=Operation.OBSERVE,
        expected_upload_id=upload,
        expected_identifier=EXTERNAL,
    )
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        original = store.record_evidence

        def fail_duplicate(attempt_id: int, evidence: ResponseEvidence) -> None:
            if evidence.remote == Remote.DUPLICATE:
                raise sqlite3.OperationalError("synthetic evidence commit failure")
            original(attempt_id, evidence)

        monkeypatch.setattr(store, "record_evidence", fail_duplicate)
        with pytest.raises(sqlite3.OperationalError):
            engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.PROCESSING
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.DUPLICATE
    assert len(client.posts) == 1 and client.gets == ["1", "1"]
