"""Conservative upload evidence parsing; private remote text never leaves here."""

from __future__ import annotations

import html
import re

from strava.recovery import Code, Operation, Remote, ResponseEvidence, positive_id


def parse_upload_response(
    payload: object, *, operation: Operation, expected_upload_id: str | None = None
) -> ResponseEvidence:
    expected = positive_id(expected_upload_id)
    if operation == Operation.OBSERVE and expected is None:
        return ResponseEvidence(code=Code.MISSING_UPLOAD_ID)
    if not isinstance(payload, dict):
        return ResponseEvidence(upload_id=expected, code=Code.MALFORMED_RESPONSE)
    raw_id, raw_string = payload.get("id"), payload.get("id_str")
    numeric = positive_id(raw_id) if type(raw_id) is int else None
    string = positive_id(raw_string) if isinstance(raw_string, str) else None
    candidates = {v for v in (numeric, string) if v}
    malformed_id = (
        raw_id is not None and numeric is None or raw_string is not None and string is None
    )
    mismatch = len(candidates) > 1 or bool(expected and candidates and candidates != {expected})
    upload_id = expected if operation == Operation.OBSERVE else next(iter(candidates), None)
    if mismatch:
        return ResponseEvidence(
            upload_id=expected, conflicting_ids=tuple(sorted(candidates)), code=Code.ID_CONFLICT
        )
    if malformed_id:
        return ResponseEvidence(
            upload_id=upload_id,
            remote=Remote.DEFERRED if upload_id else None,
            code=Code.MALFORMED_RESPONSE,
        )
    if operation == Operation.OBSERVE and not candidates:
        return ResponseEvidence(upload_id=expected, code=Code.MISSING_UPLOAD_ID)
    raw_activity = payload.get("activity_id")
    activity = positive_id(raw_activity) if type(raw_activity) is int else None
    status, error = payload.get("status"), payload.get("error")
    if (
        not isinstance(status, str)
        or not status.strip()
        or error is not None
        and not isinstance(error, str)
        or raw_activity is not None
        and activity is None
    ):
        return ResponseEvidence(
            upload_id=upload_id,
            remote=Remote.DEFERRED if upload_id else None,
            code=Code.MALFORMED_RESPONSE,
        )
    processing_error = status.strip().lower() in {
        "error",
        "there was an error processing your activity.",
    }
    if activity:
        if error or processing_error:
            return ResponseEvidence(
                upload_id=upload_id, activity_id=activity, code=Code.OUTCOME_CONFLICT
            )
        return ResponseEvidence(
            upload_id=upload_id, activity_id=activity, remote=Remote.COMPLETED, code=Code.COMPLETED
        )
    if error:
        normalized = " ".join(re.sub(r"<[^>]*>", "", html.unescape(error)).split())
        # Our uploader sends FIT basenames. Unknown filename/prose shapes stay review-only;
        # accepting arbitrary leading words would turn "may be a duplicate" into success.
        duplicate = re.fullmatch(
            r"([^\s<>:/\\]+\.fit(?:\.gz)?) duplicate of activity ([0-9]+)", normalized
        )
        duplicate_id = positive_id(duplicate.group(2)) if duplicate else None
        if duplicate and duplicate_id and upload_id and processing_error:
            return ResponseEvidence(
                upload_id=upload_id,
                duplicate_activity_id=duplicate_id,
                remote=Remote.DUPLICATE,
                code=Code.DUPLICATE,
            )
        if "duplicate" in normalized.lower():
            return ResponseEvidence(upload_id=upload_id, code=Code.DUPLICATE_UNRECOGNIZED)
        if upload_id and processing_error:
            return ResponseEvidence(
                upload_id=upload_id, remote=Remote.PROCESSING_FAILED, code=Code.PROCESSING_ERROR
            )
        return ResponseEvidence(upload_id=upload_id, code=Code.MALFORMED_RESPONSE)
    if processing_error:
        return ResponseEvidence(upload_id=upload_id, code=Code.MALFORMED_RESPONSE)
    if upload_id:
        return ResponseEvidence(upload_id=upload_id, remote=Remote.PROCESSING, code=Code.PROCESSING)
    return ResponseEvidence(code=Code.MISSING_UPLOAD_ID)
