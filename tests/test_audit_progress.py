"""CLI rendering checks for migration audit progress."""

from pathlib import Path

from typer.testing import CliRunner

from core.cli import app


def test_empty_audit_has_bounded_static_progress_and_summary(tmp_path: Path) -> None:
    source = tmp_path / "empty-export"
    source.mkdir()
    output = tmp_path / "workspace"

    result = CliRunner().invoke(app, ["audit", str(source), "--output", str(output)])
    output_text = " ".join(result.output.split())

    assert result.exit_code == 0
    assert "Analyzing Polar export" in result.output
    assert "Discovering training sessions" in result.output
    assert "Discovered 0 training session(s)" in result.output
    assert "Processing activities" in result.output
    assert "Generating migration reports" in result.output
    assert "Writing migration manifest" in result.output
    assert "Polar migration audit complete" in output_text
    assert "Activities discovered" in result.output
    assert "0" in result.output
    assert "\x1b[" not in result.output
    assert (output / "migration-audit.md").exists()


def test_failed_activity_reaches_completion_summary(tmp_path: Path) -> None:
    source = tmp_path / "export"
    source.mkdir()
    (source / "training-session-broken.json").write_text("{bad", encoding="utf-8")
    output = tmp_path / "workspace"

    result = CliRunner().invoke(app, ["audit", str(source), "--output", str(output)])

    assert result.exit_code == 1
    assert "Discovered 1 training session(s)" in result.output
    assert "Activities discovered" in result.output
    assert "Failed" in result.output
    assert "1" in result.output
    assert "training-session-broken.json" not in result.output
    assert "\x1b[" not in result.output
