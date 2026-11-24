# Desktop contract 1.0: example events

Illustrative JSON Lines (one object per line). Values are synthetic. See
[SPEC-004](../specs/004-desktop-integration-contract.md); field mapping in the
[parity matrix](desktop-parity-matrix.md). No example contains tokens, secrets or raw responses.

## Audit

```json
{"contract":"1.0","type":"event","seq":3,"time":"2026-11-24T10:00:03Z","command":"audit","run_id":"r1","event":"audit.phase","phase":"processing_activities","total":2928}
{"contract":"1.0","type":"event","seq":57,"time":"2026-11-24T10:01:10Z","command":"audit","run_id":"r1","event":"audit.progress","completed":120,"total":2928,"percent":4.1,"fit_valid":118,"fit_generated":118,"fit_reused":0,"warnings":41,"failed":2,"skipped_existing":0,"elapsed_s":67.2,"rate_per_s":1.79,"eta_s":1570.0}
{"contract":"1.0","type":"event","seq":58,"time":"2026-11-24T10:01:11Z","command":"audit","run_id":"r1","event":"audit.activity","stable_activity_id":"sha256:ab12...","source":"sub1/training-session-2018-00001.json","status":"converted","migration_status":"eligible_with_warnings","migration_reason":null,"warnings":["missing_hr"],"validation_issues":[],"counts":{"source":{"gps":3600},"domain":{"gps":3600},"fit":{"gps":3600}},"fit":{"valid":true,"sha256":"9f...","size_bytes":412345,"reused":false}}
```

Early in a run `eta_s` is `null` (CLI rule: at least 5 completed and 2 s processing).

## Upload progress and recovery event

```json
{"contract":"1.0","type":"event","seq":12,"time":"2026-11-24T11:00:00Z","command":"upload.run","run_id":"r2","event":"upload.progress","eligible":2900,"resolved":410,"percent":14.14,"completed":405,"duplicate":5,"remaining":2490,"ready_to_submit":2480,"observing":3,"needs_review":2,"local_blocked":0,"capacity_jobs":4,"duplicate_review_stops":1,"outside_current_eligible":0,"outside_manifest":0,"outside_resolved":0,"fit_bytes":1204000000,"run_done":10,"run_total":50,"current":"sha256:cd34...","evidence":"local_only","semantics":["categories_count_activities_and_may_overlap","capacity_and_review_stop_count_attempts","workspace_counts_may_exceed_batch"]}
{"contract":"1.0","type":"event","seq":13,"time":"2026-11-24T11:00:02Z","command":"upload.run","run_id":"r2","event":"upload.event","identifier":"sha256:cd34...","action":"observe","reason_codes":[],"review_upload_ids":[],"next_poll_at":"2026-11-24T11:00:12Z","wait_reason":"processing","polls_remaining":38,"failures_remaining":5,"batch":null}
```

## Rate snapshot and wait

```json
{"contract":"1.0","type":"event","seq":14,"time":"2026-11-24T11:00:03Z","command":"upload.run","run_id":"r2","event":"rate.snapshot","overall":{"short":{"usage":88,"limit":100,"remaining":12,"known":true},"daily":{"usage":410,"limit":1000,"remaining":590,"known":true}},"read":{"short":{"usage":null,"limit":null,"remaining":null,"known":false},"daily":{"usage":null,"limit":null,"remaining":null,"known":false}},"reserve":10,"max_in_flight":3,"in_flight":2}
{"contract":"1.0","type":"event","seq":15,"time":"2026-11-24T11:00:04Z","command":"upload.run","run_id":"r2","event":"rate.wait","reason":"short_window_reserve","seconds":661.0,"resume_at":"2026-11-24T11:15:01Z"}
```

## Error

```json
{"contract":"1.0","type":"error","seq":16,"time":"2026-11-24T11:00:05Z","command":"upload.run","run_id":"r2","error":{"code":"daily_limit_reached","message":"daily API safety reserve reached; resumes after midnight UTC","field":null,"details":{"resume_after":"2026-11-25T00:00:00Z"},"retryable":false}}
{"contract":"1.0","type":"error","seq":1,"time":"2026-11-24T12:00:00Z","command":"upload.run","run_id":"r3","error":{"code":"workspace_locked","message":"Another writer holds this workspace.","field":null,"details":{"holder_started_at":"2026-11-24T11:58:00Z"},"retryable":true}}
```

`retryable` refers to re-running the command later, never to re-sending an uncertain upload.

## Final results

```json
{"contract":"1.0","type":"result","seq":120,"time":"2026-11-24T10:26:00Z","command":"audit","run_id":"r1","status":"completed","summary":{"discovered":2928,"processed":2928,"validated":2890,"ready_to_migrate":2880,"needs_configuration":14,"excluded":22,"failed":24,"warnings_total":1011,"elapsed_s":1560.4,"workspace":"<workspace>","report_path":"<workspace>/migration-audit.md"}}
{"contract":"1.0","type":"result","seq":40,"time":"2026-11-24T11:30:00Z","command":"upload.run","run_id":"r2","status":"completed","summary":{"selected":50,"unattempted":0,"stopped":false,"batch":{"review":2,"observing":3,"capacity_jobs":4,"review_stops":1,"unattempted":0}},"next":"status_details"}
{"contract":"1.0","type":"result","seq":41,"time":"2026-11-24T11:31:00Z","command":"upload.run","run_id":"r4","status":"cancelled","summary":{"unattempted":31,"stopped":true},"notice":"uncertain_outcomes_are_never_retried_automatically"}
```
