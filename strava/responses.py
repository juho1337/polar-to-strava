"""Conservative upload evidence parsing; private remote text never leaves here."""

from __future__ import annotations

import html
import re

from strava.recovery import Code, Operation, Remote, ResponseEvidence, positive_id


def is_upload_envelope(payload: object) -> bool:
    """Require upload-shaped fields before interpreting unexpected HTTP body IDs.

    Expected success HTTP supplies operation context for partial parsing. An error
    response does not: a generic error object's positive ``id`` is not an upload ID.
    Identity consistency remains the parser's job, so attributable conflicts survive.
    """
    if not isinstance(payload, dict):
        return False
    status, error, activity = (
        payload.get("status"),
        payload.get("error"),
        payload.get("activity_id"),
    )
    return (
        isinstance(status, str)
        and bool(status.strip())
        and (error is None or isinstance(error, str))
        and (activity is None or type(activity) is int and positive_id(activity) is not None)
    )


def linked_duplicate_id(error: str, expected_identifier: str | None) -> str | None:
    """Recognize only SPEC-002's complete assertion; HTML is never executed/repaired."""
    if expected_identifier is None or not re.fullmatch(
        r"sha256:[0-9a-f]{64}\.fit", expected_identifier
    ):
        return None
    prefix = expected_identifier + " duplicate of "
    if not error.startswith(prefix):
        return None
    anchor = re.fullmatch(r"<a(?P<attrs>[^<>]+)>(?P<title>[^<>]*)</a>", error[len(prefix) :])
    if anchor is None:
        return None
    attributes: dict[str, str] = {}
    remaining = anchor["attrs"]
    while remaining:
        attribute = re.match(r"\s+([a-z]+)=([\"'])(.*?)\2", remaining)
        if attribute is None or attribute[1] in attributes:
            return None
        attributes[attribute[1]] = attribute[3]
        remaining = remaining[attribute.end() :]
    if set(attributes) not in ({"href"}, {"href", "target"}):
        return None
    if "target" in attributes and attributes["target"] != "_blank":
        return None
    path = re.fullmatch(r"/activities/([1-9][0-9]*)", attributes["href"])
    return path[1] if path else None


def parse_upload_response(
    payload: object,
    *,
    operation: Operation,
    expected_upload_id: str | None = None,
    expected_identifier: str | None = None,
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
        linked_id = linked_duplicate_id(error, expected_identifier)
        if (
            linked_id
            and upload_id
            and status.strip() == "There was an error processing your activity."
            and "activity_id" in payload
            and raw_activity is None
            and payload.get("external_id") == expected_identifier
        ):
            return ResponseEvidence(
                upload_id=upload_id,
                duplicate_activity_id=linked_id,
                remote=Remote.DUPLICATE,
                code=Code.DUPLICATE,
            )
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
