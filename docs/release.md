# Release preparation

## Version and workflow

The proposed release is **0.2.0**, tag **v0.2.0**, title **Polar Activity Migrator 0.2.0**.
It remains an unpublished release candidate. This follows the existing minor-version
recommendation in the [SPEC-001 plan](../specs/001-strava-uploader-recovery-plan.md).
Schema-2 recovery, conservative retry/reset semantics and changed reporting justify a
minor release while known operational limitations remain explicit. Python 3.12+ and
GPL-3.0-only are declared in `pyproject.toml`; `LICENSE` contains GPLv3.

`pyproject.toml` is the version/dependency authority. `requirements.txt` delegates to
the editable development install. Runtime installation uses `pip install -e .` and
development uses `pip install -e ".[dev]"`. Setuptools exposes `polar-to-strava` and
bundles both TCX schemas. The [changelog](../CHANGELOG.md) is the release-history
mechanism; mark its candidate entry with the publication date when publishing.
No tracked GitHub release workflow or CI exists; validation is local and release
publication is manual. Do not claim automated CI or a published package.

History uses development branches and merge commits into `main`. Recommend a reviewed
merge commit from `spec-001-foundation` into `develop`, then from `develop` into `main`,
retaining implementation, approval and verification history. The local `main` pointer
is stale; inspect/fetch remote state before release actions. No remote operation is
part of this preparation.

## Readiness review — 2026-10-02

Starting branch: `spec-001-foundation`; starting working tree: clean.
Reviewed all 21 commits in `d9b0027..0aa4f77` since divergence from local `develop`,
and all 29 commits in `origin/main..0aa4f77`, including the earlier SDD/approval work.
The merge base with `origin/main` is `8155f83`; the latest known release is `v0.1.1`
at `6d80eb0`. These are local references; no fetch was performed.

| Classification | Finding and disposition |
| --- | --- |
| Release blocker | None found in the local readiness review. |
| Should fix before release | Stale live-acceptance prerequisites in README, setup, operations, troubleshooting and architecture: replaced with separately attributed human evidence. |
| Should fix before release | Legacy requirements omitted fit-tool: delegated to the authoritative development extra, removing duplicated dependency declarations. |
| Should fix before release | Ignore rules omitted token temporary files, SQLite sidecars/upgrade backups and TCX: covered common generated names; arbitrary private locations still require review. |
| Should fix before release | Package version lagged existing tags and lacked a release-history/checklist mechanism: prepare 0.2.0 after readiness checks, add this checklist and changelog. |
| Documented limitation | Conservative uncertainty/review, schema upgrade/older reader, token/account and durability/concurrency limits below. |
| Future work | Audit performance and already documented follow-ups below; no future behavior implemented. |

[SPEC-001](../specs/001-strava-uploader-recovery.md) is Verified with AC-01–AC-18
evidence. [SPEC-002](../specs/002-strava-duplicate-response-and-polling-termination.md)
is Verified with AC-01–AC-07 evidence, including the approved 2026-10-01 capacity
amendment (`8510349`, verification `f749e0f`). Their approved behavior, criteria and
historical synthetic results remain unchanged. This release review adds no specification.
The reporting regression is restored in **0aa4f77b960af2331d4a3ba1a3f5d59aa3ff9964**:
the renderer reads the client's current rate snapshot through final rendering; CLI and
renderer regressions verify overall/read used/limit display without changing scheduling.

No temporary execution guards, pytest skips/xfails, deferred tests, breakpoints or
temporary debug diagnostics remain. Both production adapters use the recovery runner;
the sole activity-submission call remains behind durable intent and artifact checks.
No architecture, source interpretation, eligibility, identity, manifest, state schema,
retry permission or rate scheduling change is introduced by release preparation.

## Human-provided controlled live acceptance

The requesting maintainer supplied this evidence on 2026-10-02. It was not independently
replayed or read from private files during release preparation. No live identifiers,
names, secrets or workspace paths are included here.

- Initial single/five-activity checks established new upload, restart-safe resolution,
  real duplicate response discovery and conservative handling of an unknown format.
- After SPEC-002 and its capacity amendment, all five selected activities were processed:
  one completed, four duplicates; zero observations, capacity jobs or unattempted selected
  submissions remained. Three historical duplicate review stops remained without capacity.
- The 25-activity batch ended at 57 resolved (14 completed, 43 duplicates), with zero
  observations, capacity jobs and unattempted selected submissions. Restored rate counters
  were visible in actual execution.
- The 70-activity batch reached the configured reserve at 190/200 overall short-window
  usage, waited and automatically resumed. It ended at 127 resolved (30 completed,
  97 duplicates), with zero observations, capacity jobs and unattempted selected
  submissions. Three historical review stops remained safely retained.
- A subsequent full-selection run was deliberately interrupted with Ctrl+C at
  334 resolved (75 completed, 259 duplicates), retaining two observations/two remote
  jobs and three duplicate review stops. It ended cleanly with work retained. The
  reported run stop was the intentional interruption, not an unexplained scheduler stop.

This supports live wait/resume, terminal resolution and interruption evidence. It does
not establish complete migration of every activity, post-interruption completion,
exactly-once behavior or hardware power-loss durability.

## Confirmed limitations and future work

The [uploader guide](strava-uploader.md), [architecture](architecture.md),
[privacy guidance](privacy.md) and [FIT guide](fit-export.md) remain authoritative:

- No exactly-once guarantee; uncertain submissions are not blindly resent or automatically
  searched/reconciled. Persistent review states and processing failures need human review.
- Historical `duplicate_unrecognized` responses were discarded; upgrading cannot
  reconstruct or resolve them. New unknown variants remain conservative.
- One uploader process and intact history are required. Concurrent writers are unsupported;
  hardware power-loss durability, stale restores and cross-workspace deduplication are
  outside guarantees.
- Tokens are plaintext with no application permission hardening/encryption or historical
  athlete binding. Manual OAuth generates a state nonce but does not verify returned state.
- Archived schema-1 readers may add an empty legacy table before refusing schema 2,
  causing current mixed-layout refusal. There is no supported downgrade or automatic restore.
- Rate limits can make migration take days. Invalid/ineligible or ambiguous source records
  remain excluded or require verified configuration. FIT precision and sport mappings
  have format limits; TCX's Strava continuous-HR display limitation remains documented.

Audit/analyze performance is retained in
[SPEC-002 future work](../specs/002-strava-duplicate-response-and-polling-termination.md#future-work-recommendation-audit-performance):
the reported audit of 2,928 activities took **45m 03s**. Profile and reduce runtime while
preserving FIT validation, integrity, deterministic output and manifest equivalence.
Existing follow-ups remain in their original documents: CI setup in
[contributing](../CONTRIBUTING.md#recovery-changes), ambiguous power semantics in
[altitude/power](polar-altitude-power.md#power-remains-unresolved), shared lap boundaries
and byte-identical source identity in architecture, and recovery/account/storage
extensions in SPEC-001. No new roadmap or implementation is introduced.

## Release validation checklist

Before metadata changes, review branch/history, specs/compliance, reporting restoration,
guards/tests/debug code, private artifacts, dependencies, installation/CLI/OAuth/docs,
limitations, Python support, license and version/release conventions. Stop on a blocker.
Before committing the candidate, run the [required checks](testing.md) from the root:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -p no:cacheprovider
& '.\.venv\Scripts\ruff.exe' check .
& '.\.venv\Scripts\black.exe' --check . --workers 1
& '.\.venv\Scripts\mypy.exe' .
& '.\.venv\Scripts\python.exe' -m pip check
git diff --check
```

Also check local Markdown paths/anchors, compare CLI help to examples, review all tracked
files and candidate diffs for secrets/private/generated data, inspect reachable history
for private artifact names, and check ignore coverage for actual token/state/backup names.
Never read a real workspace/token file or run OAuth/upload as routine release validation.
These privacy checks reduce exposure risk; they are not a guarantee against arbitrary
secret formats or deliberately forced tracking.

Fresh candidate validation on 2026-10-02:

| Check | Result |
| --- | --- |
| Full pytest | **423 passed in 20.53s**, zero skips/xfails/failures. Includes the two added rate-reporting regressions beyond the previous 421-test result. |
| Ruff | Passed, zero findings. |
| Black | Passed, **77 files** unchanged (`--workers 1`). |
| mypy | Passed, **77 source files**. |
| Documentation | **27 Markdown files, 144 local links, 15 anchors**, zero errors; existing diagrams unchanged. |
| Whitespace | `git diff --check` passed. |
| Dependencies | `pip check` passed; all **7 runtime declarations** match installed package metadata. Requirements delegates to the same dev extra. |
| Packaging | Editable installation reports **0.2.0**; wheel build passed, **57 members** checked for version, Python requirement, GPL license, CLI entry point, both XSDs and absence of private/test/build artifacts. |
| CLI/documentation | Root, audit, upload, auth and reset help matched documented commands/defaults; status/dry-run/reset behavior also covered by the suite. Setup's code/scope prompts and token rotation match implementation. |
| Privacy/artifacts | **119 candidate tracked files**, zero forbidden private artifact paths or credential-pattern candidates. **53 reachable commits / 337 text blobs** checked with zero historical private artifact paths or credential-pattern candidates. Synthetic/public-safe fixture shapes reviewed; no real credentials found. |
| Ignore coverage | **18/18 synthetic artifact paths** covered, including tokens and temporary replacements, SQLite state/sidecars/backups, FIT/TCX, reports/config, environment and common export/workspace names. |
| Compliance review | Existing SPEC-001/SPEC-002 evidence and invariants retained; no behavioral deviations, architecture changes or new specification. Independent read-only review found no critical defect; its validation-placeholder and Ctrl+C-wording findings were resolved before committing. |

Environment adjustments: sandbox access to pytest/pip temporary directories required
approved execution outside the sandbox. Pytest cache was disabled without dropping tests.
Pip build isolation obtained the declared setuptools backend because it was absent from
the existing environment; runtime dependencies were unchanged. The local wheel build
created `build/` copies that caused a mypy duplicate-module error; removing that generated
directory restored the documented full `mypy .` check, with no configuration/code change.
The first sandbox pytest run's temporary-directory setup errors were environmental;
both subsequent full runs passed (423 tests, 22.16s and 20.53s).

Release preparation did not access a real migration workspace or real credentials,
make a real Strava/OAuth request, push, merge, create a tag or publish a release. A local
wheel is an ignored validation artifact, not a published distribution. Keep the branch
and workspace in place; remote freshness and final merge results need review at the
separately authorized release step.

## Later release actions — require explicit authorization

Preparation stops with clean local commits. Do not execute these commands as part of
preparation. Fetch and inspect remote divergence first; stop if it requires resolution.
After approval, push the candidate and open/merge reviewed PRs with merge commits:

```powershell
git fetch origin
git log --oneline --left-right develop...origin/develop
git log --oneline --left-right main...origin/main
git push -u origin spec-001-foundation
$candidatePr = gh pr create --base develop --head spec-001-foundation --title 'Prepare 0.2.0 recovery release' --body 'Verified recovery, duplicate handling and release preparation. See docs/release.md and CHANGELOG.md for evidence and compatibility.'
gh pr merge $candidatePr --merge
$releasePr = gh pr create --base main --head develop --title 'Release 0.2.0' --body 'Release the verified 0.2.0 candidate. See CHANGELOG.md and docs/release.md.'
```

Before merging the release PR, replace `Unreleased` in the changelog with the actual
publication date through a reviewed commit on `develop`, rerun checks, and review the
final release diff. Then, with explicit merge/tag/publication authorization:

```powershell
gh pr merge $releasePr --merge
git fetch origin
git switch main
git merge --ff-only origin/main
git status --short
git tag -a v0.2.0 -m 'Polar Activity Migrator 0.2.0'
git push origin v0.2.0
gh release create v0.2.0 --verify-tag --title 'Polar Activity Migrator 0.2.0' --notes 'Local Polar discovery/audit and validated FIT artifacts; Strava OAuth/upload with durable recovery, resume, duplicate recognition, bounded processing, rate-limit reporting and automatic short-window wait/resume. Status, dry-run and safe reset retain review evidence. Human-controlled acceptance exercised duplicates, rate waits and Ctrl+C. Python 3.12+; GPL-3.0-only. Preserve workspace history and use one uploader process. Uncertain submissions require review; no automatic reconciliation or exactly-once guarantee. Tokens are plaintext; hardware power-loss durability is unproven. Schema-2 state must not be reopened with older binaries. See CHANGELOG.md and docs/strava-uploader.md.'
```

Only tag a clean, validated `main` containing the reviewed candidate with version `0.2.0`.
No wheel/PyPI publication is implied; the supported installation is from the repository.
