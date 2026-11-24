"""Diagnostic text in exported reports must not carry local filesystem paths."""

from __future__ import annotations

from integration.commands_migration import scrub_diagnostics

WORKSPACE = "C:\\Users\\Example Person\\migration workspace"


def test_known_roots_with_spaces_are_replaced_in_both_slash_styles() -> None:
    text = (
        "PolarImportError: C:\\Users\\Example Person\\migration workspace\\src\\a.json invalid; "
        "also C:/Users/Example Person/migration workspace/src/b.json"
    )
    cleaned = scrub_diagnostics(text, (WORKSPACE,))
    assert "Example Person" not in cleaned
    assert "invalid" in cleaned and "<path>" in cleaned


def test_unknown_absolute_paths_are_replaced() -> None:
    drive = scrub_diagnostics("failed D:\\data\\export\\x.json: bad")
    unc = scrub_diagnostics("failed \\\\server\\share\\export\\x.json: bad")
    posix = scrub_diagnostics("failed /home/someone/export/x.json: bad")
    for cleaned in (drive, unc, posix):
        assert "x.json" not in cleaned and "<path>" in cleaned and "bad" in cleaned


def test_ordinary_text_timestamps_and_relative_paths_are_untouched() -> None:
    text = "and/or fit/running 2020-01-01T10:00:00+02:00 relative/path/file.json ratio 3/4"
    assert scrub_diagnostics(text) == text


def test_scrubbing_recurses_through_structures_and_keeps_other_values() -> None:
    value = {
        "migration": {"reason": f"error in {WORKSPACE}\\a.json", "status": "failed"},
        "examples": [{"error": "C:\\temp\\x.json bad"}, 7, None],
    }
    cleaned = scrub_diagnostics(value, (WORKSPACE,))
    assert cleaned["migration"]["status"] == "failed"
    assert "Example Person" not in str(cleaned) and "C:\\temp" not in str(cleaned)
    assert cleaned["examples"][1:] == [7, None]
