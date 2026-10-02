# Changelog

Release entries describe user-visible behavior and compatibility. Package versions and
new tags use `MAJOR.MINOR.PATCH` and `vMAJOR.MINOR.PATCH`. Existing tags are unchanged.

## 0.2.0 — Unreleased

- Discover and audit Polar training exports locally; produce validated FIT migration
  artifacts, review reports and a content-identified manifest. TCX conversion remains
  available. Supported measurements are preserved; ambiguous or invalid data is reported
  or excluded instead of fabricated.
- Authorize Strava uploads with the manual OAuth workflow and verify FIT hashes before
  submission. Durable SQLite recovery state retains submission intent, remote IDs and
  outcomes across restart and Ctrl+C.
- Recognize narrowly validated Strava duplicates. Unrecognized duplicate responses stay
  visible for review, receive no automatic requests and consume no submission capacity.
- Handle observed overall/read rate limits, show used/limit counters and automatically
  wait/resume at short-window reserves. Bound remote processing and observation; daily
  reserves and HTTP 429 preserve work for a later run.
- Inspect saved status and dry-run actions locally. Safe reset preserves history and
  uncertainty; `--force` is a deprecated no-op.
- Align the legacy requirements entry point with project metadata and extend artifact
  ignore rules to TCX, token temporary files, SQLite sidecars and upgrade backups.

Compatibility: Python 3.12+; GPL-3.0-only. Manifest version 1 and content identity remain
unchanged. Uploader state upgrades to schema 2 after a private backup. Do not reopen
upgraded state with older binaries or restore stale state after remote activity.
Internal upload/callback APIs and status/dry-run output changed with the recovery model.

Recovery is conservative: unknown submissions and retained review states may require
human intervention; no automatic reconciliation or exactly-once guarantee is provided.
Historical unrecognized duplicates cannot be reconstructed from discarded responses.
Use one uploader process and preserve workspace history. Tokens are plaintext and are
not bound to a stored athlete identity. Hardware power-loss durability is unproven.
Large migrations can span days under Strava limits, and invalid/ineligible Polar
activities remain outside automatic migration. See [operations](docs/strava-uploader.md)
and [release evidence](docs/release.md) for details.

## 0.1.1 — Historical tag `v0.1.1`

- Improved audit progress reporting.
- Historical package metadata remained `0.1.0`; this entry records the existing tag.

## 0.1.0 — Historical tag `v.0.1.0`

- Initial tagged Polar discovery, conversion, FIT audit/workspace and Strava upload
  workflow, with installation and migration documentation.
