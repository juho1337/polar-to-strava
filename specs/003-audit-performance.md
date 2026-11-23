# SPEC-003: Audit performance

Status: Approved
Created: 2026-11-24
Approval: 2026-11-24, requesting user approved the specification and its D decisions with the stated recommendations (D1, D2 opt-in, D4 yes; D3 out of scope)
Related: [SPEC-004](004-desktop-integration-contract.md), [baseline](../docs/performance-baseline.md)
Origin: [SPEC-002 future work](002-strava-duplicate-response-and-polling-termination.md#future-work-recommendation-audit-performance) (2,928 activities in 45m 03s)

This specification follows the [SDD workflow](README.md). CURRENT is measured on synthetic
data only (`benchmarks/`). TARGET is proposed and needs approval before implementation.

## Problem

`audit` converts and verifies every source on every run. A reported audit of 2,928
activities took 45m 03s (about 7.4 worker-seconds per activity at 8 workers).

## CURRENT (measured, see baseline document)

Per activity, single process, 1 Hz synthetic 30-180 min sessions:

| Stage | Share |
| --- | ---: |
| `FITBuilder.build` (encode + internal decode/verify round trip) | ~61% |
| `_decode_fit` (second decode of the file just written) | ~37% |
| JSON load, source info/metadata (3 extra JSON parses), parse, validate, SHA-256 | ~2% |

Findings that motivate the design:

1. A freshly generated FIT is fully decoded and compared twice: in `FITBuilder.build`
   (bytes) and in `_decode_fit` (file). The decode itself is `fit-tool` per-message object
   construction and dominates; JSON parsing is not a bottleneck.
2. A warm rerun (`skipped_existing`) re-parses sources and fully re-decodes every FIT.
3. Worker count is capped at 8 regardless of cores; `executor.map` uses chunksize 1 and
   pickles the importer/validator per task. Peak tree memory was ~4 GB (about 280 MB per
   large activity) so concurrency must stay memory-bounded.
4. Reports and manifest are written in place and non-atomically (a crash can leave a mix
   of old and new manifest/report files).
5. Parser lap assignment is O(points x laps) (not yet significant at measured sizes).

## TARGET

Behavior (rows, manifest, eligibility, FIT bytes, ordering) is unchanged for equal inputs.

- **AC-01 Output equivalence.** For any input, optimized and baseline audits produce
  byte-identical FIT files, identical `migration-manifest.json/.csv` and
  `migration-config.template.json`, and identical `migration-audit.json` apart from
  timing fields. Verified by the benchmark fingerprint and regression tests.
- **AC-02 No weakened verification.** Every FIT that enters the manifest as valid has had
  CRC, `validate`, semantic and per-sample comparison performed against the domain
  activity, by the same checks as today, exactly once on the bytes stored.
- **AC-03 Single decode for generated files.** For a newly generated FIT the decoded
  messages from the builder verification are reused for the audit semantic check, and the
  bytes on disk are proven identical to the verified bytes (read-back SHA-256 equals the
  hash of the verified bytes). A mismatch is `fit_write` failure.
- **AC-04 Bounded parallelism.** Worker count defaults to `min(cpu_count, memory budget)`
  with an explicit documented option; results are collected in discovery order; memory
  stays bounded (in-flight tasks limited). Cancellation stops submitting and drains.
- **AC-05 Versioned incremental reuse (Decision D2).** An optional cache stores a prior
  audit row keyed by: source SHA-256, timezone override, `AUDIT_CACHE_VERSION`,
  converter/builder/validator/parser code versions, `fit-tool` version and FIT SHA-256.
  A hit is accepted only if the FIT file still exists and its SHA-256 and size equal the
  cached values. Any key change, missing/corrupt/unknown-version cache or mismatch is a
  miss and the full pipeline runs. The cache never changes manifest semantics.
- **AC-06 Existing-FIT semantics preserved.** Without the cache, `skipped_existing`
  continues to fully validate the existing file (no trust by filename).
- **AC-07 Atomic outputs.** Every report/manifest file and FIT is written to a temporary
  file in the destination directory and `os.replace`d; the manifest set is replaced last.
  An interrupted audit never leaves a truncated file; stale temp files are ignored/cleaned.
- **AC-08 Deterministic order and identity.** Row order, `stable_activity_id`, fit file
  names and manifest schema (`MANIFEST_VERSION`) are unchanged.
- **AC-09 Uploader isolation.** No change to `strava/`; no network access in tests.
- **AC-10 Measurement.** Benchmark harness reports wall, CPU, peak memory, I/O, cold/warm,
  and equivalence fingerprints; before/after results recorded in the baseline document.
  The 3x goal is reported honestly; it is not an acceptance gate.

## Decisions requiring approval

- **D1** Reuse the builder's verified decode for the audit check (AC-03). Recommended:
  yes; saves ~37% with unchanged checks. (Requires `FITBuilder` to expose the decoded
  messages; `FITBuilder.build` stays source compatible.)
- **D2** Cache-based incremental audit (AC-05). Validation is a pure function of
  (source bytes, override, code versions, FIT bytes), so hash-keyed reuse is sound, but it
  means a warm run no longer re-decodes unchanged FIT files. Recommended: opt-in
  `--incremental` first; default stays full verification. Alternative: no cache.
- **D3** Replacing `fit-tool` decoding with a custom reader is out of scope (risk to
  verification independence). Revisit only with a differential test corpus.
- **D4** Worker default above 8 (AC-04). Recommended: yes, memory-bounded.

## Architecture impact

`services/audit.py` (pipeline), `fit/builder.py` (expose verified decode), new
`services/audit_cache.py`, new atomic-write helper. Domain, parser semantics, `strava/`,
manifest schema and recovery state are untouched.

## Out of scope

Uploader changes, parser/semantic changes, C# rewrite, GUI.
