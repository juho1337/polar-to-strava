# Resumable Strava uploader

The uploader consumes `migration-manifest.json` and workspace FIT files. It never
parses Polar JSON or changes migration eligibility. The uploader is designed to
prevent automatic duplicate resubmission when an earlier upload's result is uncertain.
It does not promise exactly-once delivery. Use one process and preserve workspace history.

**Development checkpoint:** production upload remains guarded while final SPEC-001
compliance is reviewed. Local status, preview and safe reset are available. No controlled
live acceptance of SPEC-001 has been performed. Do not use this branch for real migration.

## Review local recovery

```powershell
python main.py strava status "<workspace>"
python main.py strava status "<workspace>" --details
python main.py strava upload "<workspace>" --dry-run --limit 5
python main.py strava upload "<workspace>" --dry-run --all
```

These commands make no Strava request, refresh no token, and create no submission intent.
They can initialize, upgrade or reconcile local state. Preview also verifies selected
local artifacts and can record discovered blockers. Status reflects saved evidence,
not a fresh check of Strava or every FIT file.

| Action/outcome | Meaning |
| --- | --- |
| Ready to submit / `would_submit` | Durable provenance permits a candidate; local preview checks artifacts. Any later POST requires fresh access, rate, artifact and transactional permission checks. Acceptance is not promised. |
| Observing / `would_observe` | A known upload can be observed with GET, or awaits permitted observation. Missing/changed FIT and removed/ineligible entries do not authorize POST or prevent trustworthy GET. |
| Needs review / blocked | Uncertain history, processing failure, unsafe identity or a local/access problem needs attention. Acknowledgment or elapsed time cannot grant resend permission. |
| Resolved | Saved authoritative completion or duplicate evidence. Completed means an activity was created; duplicate means the upload was authoritatively identified as already present. |

Progress uses **resolved current eligible activities / current eligible activities**.
Completed and duplicate are reported separately; their union counts each activity once.
Review/local-blocker categories can overlap observation and resolution. Retained records
outside the current eligible population, including removed entries, are shown separately.
Details show fixed safe reasons, submission and remote evidence, local blockers, retained
date availability and outside-manifest membership; they do not dump raw server errors.

## Selection and resume

Exactly one of `--limit N`, `--activity-id ID`, or `--all` is required.
Limits count submission candidates only and retain matching observations. An explicit
stable ID can select a retained record even if it is absent from the manifest. `--all`
includes retained recovery work. Optional `--from`/`--to` dates use current or retained
start metadata; a missing date is explained, never guessed. Use explicit ID or `--all`
without date filters for a record whose date is unavailable.

After production activation and separately authorized live acceptance, rerunning
`strava upload "<workspace>" --all` resumes only allowed work. Known IDs route to GET,
review or resolution, never another POST because polling failed. No-ID uncertainty
remains review-only; there is no remote search or automatic reconciliation feature.
Authoritative processing failure also remains review-only, retaining its ID and outcome.

The synchronous scheduler defaults to three remote jobs (`--max-in-flight`, range 1–10).
Restored known jobs are retained even above a reduced capacity. Deferred remote work
continues to consume capacity; a stalled run exits rather than spinning. A new job's
first poll waits two seconds (minimum one); restored jobs are immediately due under
rate policy. Backoff doubles to thirty seconds. Each job has at most sixty GETs per run
and three consecutive network/server failures; successful pending observation resets
the failure counter. These are GET budgets, never POST retry counters.

Overall and read-specific response headers drive `--rate-limit-reserve` (default ten).
Short reserves wait to the next natural quarter-hour plus one second; daily reserves
stop until midnight UTC. HTTP 429 stops the batch and preserves evidence. Missing rate
headers do not create guessed quotas. Restart gives permitted observations a new bounded
budget; it does not turn an uncertain POST into a candidate.

## Safe reset

```powershell
python main.py strava reset "<workspace>" --activity-id sha256:...
```

Reset rechecks corrected local conditions and reports the safest action. It preserves
attempts, IDs, terminal outcomes, uncertainty and unknown history. `--force` is accepted
only as a deprecated no-op; it cannot permit resend. Reset never deletes a remote activity.
After OAuth repair, locally valid token configuration may clear access/retrieval blockers
for a bounded GET retry; it neither proves remote access nor proves non-submission.

## Persistence and upgrade

Schema 2 separates activity provenance, submission attempts/remote outcomes and blockers.
Intent commits before POST; response evidence commits before polling or callbacks.
Intent-write failure means no POST. Lost response persistence leaves the intent barrier;
restart observes a saved ID or requires review. Ctrl+C stops scheduling; abrupt exits
recover from committed evidence, not assumed cleanup or assumed non-submission.

Opening schema 1 creates a unique private sibling `migration-state.sqlite3.v1-*.backup`
using SQLite backup, then maps every row in one transaction, updating the version last.
The CLI reports the private backup location. Ambiguous legacy history stays review-only;
known IDs and recorded terminal outcomes survive. Failure before commit rolls back;
unsupported, corrupt or partial schemas are refused. No automatic restore or downgrade
exists. Never delete/edit state or restore a stale backup to unblock submission: after
remote activity that would erase duplicate-prevention evidence.

Archived schema-1 readers may create an empty `uploads` table before refusing schema 2.
They do not modify the schema-2 recovery tables, but the extra table causes the new reader
to refuse the mixed layout. Do not open upgraded workspaces with old binaries or manually
remove tables. Preserve the files and request diagnosis; there is no supported downgrade.

## Integrity, privacy and limitations

Access preparation and rate waits precede a private FIT snapshot. Its copied bytes must
match the manifest SHA-256; transport reads that same open stream without reopening the
source. Expired preparation discards the snapshot and repeats preparation/verification.
Normal exits close it; OS temporary-file cleanup after hard termination is platform
dependent. Size alone is never integrity proof. No measurement is reconstructed here.

Unexpected HTTP errors cannot establish completion. Their IDs are considered only within
an attributable upload envelope; generic error IDs do not become observation targets.
New duplicates require the narrow complete documented assertion, consistent identity and
processing-error status. Unknown wording requires review. Raw payloads are not persisted.

Keep state, backups, temporary artifacts and tokens private. Backups retain old private
diagnostics; current state uses fixed reason codes and validated IDs. Token storage is
plaintext, with no new encryption or account-binding mechanism. See [privacy](privacy.md)
and [OAuth setup](strava-setup.md). No guarantee covers concurrent writers, deleted or
externally edited history, stale restores, hardware power loss or cross-workspace deduplication.
Synthetic tests do not establish live API acceptance. See [troubleshooting](troubleshooting.md).
