"""Bulk Polar-to-FIT conversion with a per-source migration audit."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import repeat
from math import isclose
from pathlib import Path
from time import perf_counter
from typing import Any

from fit_tool.fit_file import FitFile  # type: ignore[import-untyped]
from fit_tool.profile.messages.record_message import RecordMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.session_message import SessionMessage  # type: ignore[import-untyped]

from core.contracts import ActivityImporter
from core.errors import ConfigurationError
from domain import Activity, Sport
from fit import FITBuilder, FITWriter
from fit.builder import SPORTS, fit_time
from polar import PolarImporter
from services.migration import (
    MANIFEST_VERSION,
    MigrationConfig,
    classify,
    file_sha256,
    load_migration_config,
    source_sha256,
    stable_activity_id,
)
from services.service_models import AuditPhase, AuditProgress, AuditProgressCallback
from services.validation import Validator

SENSORS = ("gps", "hr", "altitude", "distance", "speed", "cadence", "power", "temperature")
SOURCE_STREAMS = {
    "hr": "heartRate",
    "altitude": "altitude",
    "distance": "distance",
    "speed": "speed",
    "cadence": "cadence",
    "power": "power",
    "temperature": "temperature",
}


def _source_info(path: Path) -> tuple[str | None, dict[str, int]]:
    """Read only the source fields needed to inventory raw sensor availability."""
    result = {sensor: 0 for sensor in SENSORS}
    result["left_crank_power"] = 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None, result
    if not isinstance(payload, dict):
        return None, result
    sports: list[str] = []
    exercises = payload.get("exercises")
    if not isinstance(exercises, list):
        return None, result
    for exercise in exercises:
        if not isinstance(exercise, dict):
            continue
        sports.append(str(exercise.get("sport") or "UNKNOWN"))
        samples = exercise.get("samples")
        if not isinstance(samples, dict):
            continue
        for sensor, key in SOURCE_STREAMS.items():
            stream = samples.get(key)
            if isinstance(stream, list):
                result[sensor] += sum(
                    isinstance(item, dict) and item.get("value") is not None for item in stream
                )
        route = exercise.get("recordedRoute", samples.get("recordedRoute"))
        if isinstance(route, dict):
            route = route.get("points", route.get("locations"))
        if isinstance(route, list):
            result["gps"] += sum(
                isinstance(item, dict)
                and item.get("latitude", item.get("lat")) is not None
                and item.get("longitude", item.get("lon")) is not None
                for item in route
            )
            altitude_stream = samples.get("altitude")
            if not isinstance(altitude_stream, list) or not any(
                isinstance(item, dict) and item.get("value") is not None for item in altitude_stream
            ):
                result["altitude"] += sum(
                    isinstance(item, dict) and item.get("altitude") is not None for item in route
                )
        left = samples.get("leftPedalCrankBasedPower")
        if isinstance(left, list):
            result["left_crank_power"] += sum(
                isinstance(item, dict) and item.get("currentPower") is not None for item in left
            )
    return ",".join(sorted(set(sports))) if sports else None, result


def _source_metadata(path: Path) -> dict[str, Any]:
    """Extract small review fields without interpreting unresolved local time."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    exercises = payload.get("exercises")
    first = (
        exercises[0]
        if isinstance(exercises, list) and exercises and isinstance(exercises[0], dict)
        else {}
    )
    return {
        "source_local_start": payload.get("startTime", payload.get("start-time")),
        "source_timezone_offset_minutes": first.get(
            "timezoneOffset", first.get("timeZoneOffset", payload.get("timeZoneOffset"))
        ),
        "source_duration": payload.get("duration"),
        "source_distance": payload.get("distance", first.get("distance")),
        "source_calories": payload.get("kiloCalories", payload.get("calories")),
        "source_average_hr": payload.get("averageHeartRate"),
        "source_maximum_hr": payload.get("maximumHeartRate"),
    }


def _domain_counts(activity: Activity) -> dict[str, int]:
    points = activity.trackpoints
    return {
        "records": len(points),
        "gps": sum(point.location is not None for point in points),
        "hr": sum(point.heart_rate is not None for point in points),
        "altitude": sum(point.recorded_altitude_m is not None for point in points),
        "distance": sum(point.distance_m is not None for point in points),
        "speed": sum(point.speed_mps is not None for point in points),
        "cadence": sum(point.cadence is not None for point in points),
        "power": sum(point.power is not None for point in points),
        "temperature": sum(point.temperature is not None for point in points),
    }


def _decode_fit(path: Path, activity: Activity) -> tuple[dict[str, int], int]:
    """Check stored FIT CRC, conformance and all representable sensor streams."""
    fit = FitFile.from_file(str(path))  # checks CRC by default
    fit.validate(raise_on_error=True)
    messages = [item.message for item in fit.records]
    names = Counter(message.name for message in messages if hasattr(message, "name"))
    if any(
        names[name] != count for name, count in (("file_id", 1), ("session", 1), ("activity", 1))
    ):
        raise ValueError("FIT missing required file_id, session or activity message")
    if names["lap"] != len(activity.laps):
        raise ValueError("FIT lap count differs from domain")
    sessions = [message for message in messages if isinstance(message, SessionMessage)]
    expected_sport, expected_sub_sport = SPORTS[activity.sport]
    if (
        sessions[0].sport != expected_sport.value
        or sessions[0].sub_sport != expected_sub_sport.value
        or sessions[0].start_time != fit_time(activity.started_at)
    ):
        raise ValueError("FIT session sport or start differs from domain")
    records = [message for message in messages if isinstance(message, RecordMessage)]
    if len(records) != len(activity.trackpoints):
        raise ValueError("FIT record count differs from domain")
    counts = {"records": len(records)}
    for sensor, field in (
        ("hr", "heart_rate"),
        ("distance", "distance"),
        ("cadence", "cadence"),
        ("power", "power"),
        ("temperature", "temperature"),
    ):
        counts[sensor] = sum(getattr(point, field) is not None for point in records)
    counts["gps"] = sum(
        point.position_lat is not None and point.position_long is not None for point in records
    )
    counts["altitude"] = sum(point.enhanced_altitude is not None for point in records)
    counts["speed"] = sum(point.enhanced_speed is not None for point in records)
    if counts != _domain_counts(activity):
        raise ValueError("FIT sensor counts differ from domain")
    for source, record in zip(activity.trackpoints, records, strict=True):
        if record.timestamp != fit_time(source.timestamp):
            raise ValueError("FIT timestamp differs from domain")
        expected = {
            "heart_rate": source.heart_rate.bpm if source.heart_rate else None,
            "position_lat": source.location.latitude if source.location else None,
            "position_long": source.location.longitude if source.location else None,
            "enhanced_altitude": source.recorded_altitude_m,
            "distance": source.distance_m,
            "enhanced_speed": source.speed_mps,
            "cadence": round(source.cadence.rpm) if source.cadence else None,
            "power": source.power.watts if source.power else None,
            "temperature": round(source.temperature.celsius) if source.temperature else None,
        }
        for field, value in expected.items():
            actual = getattr(record, field)
            tolerance = 0.101 if field == "enhanced_altitude" else 0.02
            if (value is None) != (actual is None) or (
                value is not None and not isclose(value, actual, abs_tol=tolerance)
            ):
                raise ValueError(f"FIT {field} differs from domain")
    return counts, path.stat().st_size


def _warnings(activity: Activity, source: dict[str, int], counts: dict[str, int]) -> list[str]:
    warnings: list[str] = []
    for sensor in ("gps", "hr"):
        if counts[sensor] == 0:
            warnings.append(f"missing_{sensor}")
    if activity.distance_m == 0:
        warnings.append("zero_distance")
    if activity.distance_m is not None and activity.distance_m < 0:
        warnings.append("negative_distance")
    if activity.recorded_duration_s is None:
        warnings.append("missing_recorded_duration")
    if not activity.trackpoints:
        warnings.append("no_trackpoints")
    if activity.extensions.get("route_points_outside_exercise"):
        warnings.append("route_outside_exercise_inside_session")
    if activity.extensions.get("source_laps_unapplied"):
        warnings.append("source_laps_unapplied")
    if activity.duration.total_seconds() <= 0 or activity.duration.total_seconds() > 86400:
        warnings.append("suspicious_duration")
    if activity.distance_m == 0 and counts["gps"] > 0:
        warnings.append("gps_with_zero_distance")
    if activity.sport is Sport.OTHER or SPORTS[activity.sport][0].name == "GENERIC":
        warnings.append("generic_sport_fallback")
    if source["left_crank_power"] and not counts["power"]:
        warnings.append("unresolved_left_crank_power")
    for sensor in SENSORS:
        if sensor != "power" and source[sensor] > counts[sensor]:
            warnings.append(f"source_{sensor}_loss")
    points = activity.trackpoints
    if any(b.timestamp < a.timestamp for a, b in zip(points, points[1:], strict=False)):
        warnings.append("nonmonotonic_timestamps")
    if any(
        point.timestamp < activity.started_at or point.timestamp > activity.ended_at
        for point in points
    ):
        warnings.append("point_outside_activity_bounds")
    if any(
        point.location
        and (abs(point.location.latitude) > 90 or abs(point.location.longitude) > 180)
        for point in points
    ):
        warnings.append("invalid_gps_coordinate")
    return warnings


def _duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = sorted(
        (row for row in rows if row.get("started_at") and row.get("domain_sport")),
        key=lambda row: row["started_at"],
    )
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(valid):
        start = datetime.fromisoformat(left["started_at"])
        for right in valid[index + 1 :]:
            seconds = abs((datetime.fromisoformat(right["started_at"]) - start).total_seconds())
            if seconds > 60:
                break
            if left["domain_sport"] != right["domain_sport"]:
                continue
            duration = left.get("elapsed_s")
            other_duration = right.get("elapsed_s")
            if (
                duration is None
                or other_duration is None
                or abs(duration - other_duration) > max(60, duration * 0.05)
            ):
                continue
            distance = left.get("distance_m")
            other_distance = right.get("distance_m")
            if (
                distance is not None
                and other_distance is not None
                and abs(distance - other_distance) > max(100, distance * 0.05)
            ):
                continue
            pair = {
                "first_stable_activity_id": left["stable_activity_id"],
                "second_stable_activity_id": right["stable_activity_id"],
                "start_difference_seconds": seconds,
                "sport": left["domain_sport"],
                "duration_difference_seconds": abs(duration - other_duration),
                "distance_difference_m": (
                    abs(distance - other_distance)
                    if distance is not None and other_distance is not None
                    else None
                ),
            }
            pairs.append(pair)
            for row in (left, right):
                if "review_duplicate" not in row["warnings"]:
                    row["warnings"].append("review_duplicate")
    return pairs


def _failure_categories(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "failed":
            continue
        message = str(row.get("error") or "unknown error").split(": ", 1)[-1]
        key = f"{row.get('failure_stage')} / {row.get('error_type', 'unknown')} / {message}"
        group = categories.setdefault(key, {"count": 0, "examples": []})
        group["count"] += 1
        if len(group["examples"]) < 3:
            group["examples"].append({"source": row["source"], "error": row.get("error")})
    return dict(sorted(categories.items(), key=lambda item: -item[1]["count"]))


def _process_one(
    path: Path,
    source: Path,
    fit_directory: Path,
    overwrite: bool,
    importer: ActivityImporter,
    validator: Validator,
    fingerprint: str,
    timezone_override: str | None,
) -> dict[str, Any]:
    """Audit a single source in an isolated worker."""
    relative = path.relative_to(source).as_posix()
    stable_id = stable_activity_id(fingerprint)
    relative_hash = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:10]
    target = fit_directory / f"{fingerprint}-{relative_hash}.fit"
    sport, source_counts = _source_info(path)
    source_metadata = _source_metadata(path)
    row: dict[str, Any] = {
        "source": relative,
        "source_filename": path.name,
        "source_id": path.stem,
        "output_fit": str(target),
        "fit_relative_path": f"fits/{target.name}",
        "source_sha256": fingerprint,
        "stable_activity_id": stable_id,
        **source_metadata,
        "original_sport": sport,
        "source_counts": source_counts,
        "status": "pending",
        "failure_stage": None,
        "error": None,
        "warnings": [],
    }
    try:
        if timezone_override is not None:
            if not isinstance(importer, PolarImporter):
                raise ValueError("timezone overrides require the Polar importer")
            activity = importer.import_activity(
                path,
                MigrationConfig.model_validate(
                    {"activity_overrides": {stable_id: {"timezone_offset": timezone_override}}}
                )
                .activity_overrides[stable_id]
                .as_timezone(),
            )
            row["timezone_resolution_source"] = "manual_override"
            row["manual_override_used"] = True
        else:
            activity = importer.import_activity(path)
            row["timezone_resolution_source"] = "source"
            row["manual_override_used"] = False
    except Exception as error:
        row.update(
            status="failed",
            failure_stage="import",
            error=str(error),
            error_type=type(error).__name__,
        )
        row["timezone_resolution_source"] = "unresolved"
        row["manual_override_used"] = False
        return row
    row["parsed"] = True
    validation = validator.validate(activity)
    row["validation_issues"] = [issue.code for issue in validation.issues]
    if not validation.is_valid:
        row.update(
            status="failed",
            failure_stage="domain_validation",
            error="; ".join(issue.message for issue in validation.issues),
        )
        return row
    counts = _domain_counts(activity)
    fit_sport, fit_sub_sport = SPORTS[activity.sport]
    row.update(
        started_at=activity.started_at.isoformat(),
        resolved_utc_start=activity.started_at.astimezone(UTC).isoformat(),
        ended_at=activity.ended_at.isoformat(),
        utc_offset_minutes=int(
            (activity.started_at.utcoffset() or timedelta()).total_seconds() / 60
        ),
        elapsed_s=activity.duration.total_seconds(),
        timer_s=activity.recorded_duration_s,
        domain_sport=activity.sport.value,
        fit_sport=fit_sport.name.lower(),
        fit_sub_sport=fit_sub_sport.name.lower(),
        distance_m=activity.distance_m,
        calories=activity.calories,
        average_hr=activity.average_heart_rate_bpm,
        maximum_hr=activity.maximum_heart_rate_bpm,
        ascent_m=activity.ascent_m,
        descent_m=activity.descent_m,
        domain_counts=counts,
    )
    row["warnings"] = _warnings(activity, source_counts, counts) + [
        issue.code for issue in validation.issues if issue.severity.value == "warning"
    ]
    if target.exists() and not overwrite:
        row["status"] = "skipped_existing"
    else:
        try:
            content = FITBuilder().build(activity)
        except Exception as error:
            row.update(
                status="failed",
                failure_stage="fit_generation",
                error=str(error),
                error_type=type(error).__name__,
            )
            return row
        try:
            FITWriter().write(content, target)
        except OSError as error:
            row.update(
                status="failed",
                failure_stage="fit_write",
                error=str(error),
                error_type=type(error).__name__,
            )
            return row
        row["status"] = "converted"
    try:
        fit_counts, size = _decode_fit(target, activity)
        row.update(
            fit_counts=fit_counts,
            fit_bytes=size,
            fit_valid=True,
            fit_sha256=file_sha256(target),
        )
    except Exception as error:
        row.update(
            status="failed",
            failure_stage="fit_decode_validation",
            error=str(error),
            error_type=type(error).__name__,
            fit_valid=False,
        )
    return row


class MigrationAudit:
    """Process one workout at a time and account for every discovered source."""

    def __init__(self, importer: ActivityImporter, validator: Validator) -> None:
        self.importer = importer
        self.validator = validator

    def run(
        self,
        source: Path,
        output: Path,
        overwrite: bool = False,
        config: Path | None = None,
        progress: AuditProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        output.mkdir(parents=True, exist_ok=True)
        fit_directory = output / "fits"
        if progress:
            progress(AuditProgress(AuditPhase.DISCOVERY_STARTED))
        paths = tuple(self.importer.scan(source))
        if progress:
            progress(AuditProgress(AuditPhase.DISCOVERY_COMPLETED, total=len(paths)))
            progress(AuditProgress(AuditPhase.PREPARING_ACTIVITIES, total=len(paths)))
        fingerprints = tuple(source_sha256(path) for path in paths)
        configuration = load_migration_config(config)
        known_ids = {stable_activity_id(value) for value in fingerprints}
        unknown_ids = set(configuration.activity_overrides) - known_ids
        if unknown_ids:
            raise ConfigurationError(
                "Migration configuration contains unknown stable activity IDs: "
                + ", ".join(sorted(unknown_ids))
            )
        overrides = tuple(
            configuration.activity_overrides.get(stable_activity_id(value))
            for value in fingerprints
        )
        override_values = tuple(
            item.timezone_offset if item is not None else None for item in overrides
        )
        for path, stable_id, override in zip(paths, fingerprints, override_values, strict=True):
            if override is None:
                continue
            metadata = _source_metadata(path)
            local_start = metadata.get("source_local_start")
            timestamp_has_zone = False
            if isinstance(local_start, str):
                try:
                    timestamp_has_zone = (
                        datetime.fromisoformat(local_start.replace("Z", "+00:00")).tzinfo
                        is not None
                    )
                except ValueError:
                    pass
            if metadata.get("source_timezone_offset_minutes") is not None or timestamp_has_zone:
                raise ConfigurationError(
                    "Timezone override conflicts with authoritative source timezone for "
                    + stable_activity_id(stable_id)
                )
        if progress:
            progress(AuditProgress(AuditPhase.PROCESSING_ACTIVITIES, total=len(paths)))
        rows: list[dict[str, Any]] = []
        fit_valid = 0
        warning_count = 0
        failed = 0
        skipped_existing = 0
        with ProcessPoolExecutor(
            max_workers=max(1, min(8, len(paths), os.cpu_count() or 2))
        ) as executor:
            results = executor.map(
                _process_one,
                paths,
                repeat(source),
                repeat(fit_directory),
                repeat(overwrite),
                repeat(self.importer),
                repeat(self.validator),
                fingerprints,
                override_values,
            )
            for completed, row in enumerate(results, 1):
                rows.append(row)
                fit_valid += bool(row.get("fit_valid"))
                warning_count += len(row["warnings"])
                failed += row["status"] == "failed"
                skipped_existing += row["status"] == "skipped_existing"
                if progress:
                    progress(
                        AuditProgress(
                            AuditPhase.PROCESSING_ACTIVITIES,
                            completed=completed,
                            total=len(paths),
                            fit_valid=fit_valid,
                            warnings=warning_count,
                            failed=failed,
                            skipped_existing=skipped_existing,
                        )
                    )
        duplicate_candidates = _duplicates(rows)
        for row in rows:
            row["migration_status"] = classify(row).value
            row["migration_reason"] = row.get("error") if not row.get("fit_valid") else None
        statuses = Counter(row["status"] for row in rows)
        migration_statuses = Counter(row["migration_status"] for row in rows)
        failures = Counter(row.get("failure_stage") for row in rows if row["status"] == "failed")
        warnings = Counter(warning for row in rows for warning in row["warnings"])
        sports: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["original_sport"] or "UNKNOWN"
            sport_row = sports.setdefault(
                key,
                {
                    "activities": 0,
                    "converted": 0,
                    "failed": 0,
                    "skipped": 0,
                    "domain": Counter(),
                    "fit": Counter(),
                    "fallback": 0,
                },
            )
            sport_row["activities"] += 1
            sport_row["converted"] += row["status"] == "converted"
            sport_row["failed"] += row["status"] == "failed"
            sport_row["skipped"] += row["status"] == "skipped_existing"
            if row.get("domain_sport"):
                sport_row["domain"][row["domain_sport"]] += 1
                sport_row["fit"][f"{row['fit_sport']}/{row['fit_sub_sport']}"] += 1
            sport_row["fallback"] += "generic_sport_fallback" in row["warnings"]
        sensor_inventory = {
            sensor: {
                layer: {
                    "activities": sum(
                        row.get(f"{layer}_counts", {}).get(sensor, 0) > 0 for row in rows
                    ),
                    "samples": sum(row.get(f"{layer}_counts", {}).get(sensor, 0) for row in rows),
                }
                for layer in ("source", "domain", "fit")
            }
            for sensor in (*SENSORS, "left_crank_power")
        }
        dated = [row for row in rows if row.get("started_at")]
        years = Counter(row["started_at"][:4] for row in dated)
        months = Counter(row["started_at"][:7] for row in dated)
        duration = perf_counter() - started
        report: dict[str, Any] = {
            "source_root": str(source),
            "output_root": str(output),
            "format": "fit",
            "summary": {
                "discovered": len(rows),
                "parsed": sum(bool(row.get("parsed")) for row in rows),
                "converted": statuses["converted"],
                "validated": sum(bool(row.get("fit_valid")) for row in rows),
                "failed": statuses["failed"],
                "skipped_existing": statuses["skipped_existing"],
                "failure_stages": dict(failures),
                "failure_categories": _failure_categories(rows),
                "warning_counts": dict(warnings),
                "migration_status_counts": dict(sorted(migration_statuses.items())),
                "total_fit_bytes": sum(row.get("fit_bytes", 0) for row in rows),
                "generated_fit_bytes": sum(
                    row.get("fit_bytes", 0) for row in rows if row["status"] == "converted"
                ),
                "elapsed_seconds": duration,
                "activities_per_second": len(rows) / duration if duration else 0,
                "earliest": min((row["started_at"] for row in dated), default=None),
                "latest": max((row["started_at"] for row in dated), default=None),
                "per_year": dict(sorted(years.items())),
                "per_month": dict(sorted(months.items())),
            },
            "sports": dict(sorted(sports.items())),
            "sensors": sensor_inventory,
            "duplicate_candidates": duplicate_candidates,
            "activities": rows,
        }
        final_progress = {
            "completed": len(rows),
            "total": len(paths),
            "fit_valid": fit_valid,
            "warnings": sum(warnings.values()),
            "failed": failed,
            "skipped_existing": skipped_existing,
        }
        if progress:
            progress(AuditProgress(AuditPhase.GENERATING_REPORTS, **final_progress))
        self._write_reports(output, report)
        if progress:
            progress(AuditProgress(AuditPhase.WRITING_MANIFEST, **final_progress))
        self._write_manifest(output, source, rows, migration_statuses)
        if progress:
            progress(AuditProgress(AuditPhase.COMPLETE, **final_progress))
        return report

    @staticmethod
    def _manifest_entry(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "stable_activity_id": row["stable_activity_id"],
            "source": {
                "relative_path": row["source"],
                "filename": row["source_filename"],
                "sha256": row["source_sha256"],
                "local_start": row.get("source_local_start"),
            },
            "time": {
                "resolved_utc_start": row.get("resolved_utc_start"),
                "resolution_source": row.get("timezone_resolution_source", "unresolved"),
            },
            "sport": {
                "polar": row.get("original_sport"),
                "domain": row.get("domain_sport"),
                "fit": row.get("fit_sport"),
                "fit_sub_sport": row.get("fit_sub_sport"),
                "fallback": "generic_sport_fallback" in row["warnings"],
            },
            "summary": {
                "timer_seconds": row.get("timer_s"),
                "elapsed_seconds": row.get("elapsed_s"),
                "distance_m": row.get("distance_m", row.get("source_distance")),
                "calories": row.get("calories", row.get("source_calories")),
            },
            "fit": {
                "relative_path": row.get("fit_relative_path") if row.get("fit_valid") else None,
                "sha256": row.get("fit_sha256"),
                "size_bytes": row.get("fit_bytes"),
                "valid": bool(row.get("fit_valid")),
            },
            "migration": {
                "status": row["migration_status"],
                "warnings": sorted(row["warnings"]),
                "reason": row.get("migration_reason"),
                "manual_override_used": bool(row.get("manual_override_used")),
            },
        }

    @classmethod
    def _write_manifest(
        cls,
        output: Path,
        source: Path,
        rows: list[dict[str, Any]],
        statuses: Counter[str],
    ) -> None:
        entries = [cls._manifest_entry(row) for row in rows]
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "source": {"type": "polar_user_data_export", "workout_count": len(entries)},
            "summary": {"total": len(entries), "status_counts": dict(sorted(statuses.items()))},
            "activities": entries,
        }
        (output / "migration-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        columns = [
            "stable_activity_id",
            "source_relative_path",
            "source_filename",
            "source_sha256",
            "local_start",
            "resolved_utc_start",
            "timezone_source",
            "status",
            "warnings",
            "reason",
            "fit_relative_path",
            "fit_sha256",
            "fit_size_bytes",
            "fit_valid",
        ]
        with (output / "migration-manifest.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for entry in entries:
                writer.writerow(
                    {
                        "stable_activity_id": entry["stable_activity_id"],
                        "source_relative_path": entry["source"]["relative_path"],
                        "source_filename": entry["source"]["filename"],
                        "source_sha256": entry["source"]["sha256"],
                        "local_start": entry["source"]["local_start"],
                        "resolved_utc_start": entry["time"]["resolved_utc_start"],
                        "timezone_source": entry["time"]["resolution_source"],
                        "status": entry["migration"]["status"],
                        "warnings": ";".join(entry["migration"]["warnings"]),
                        "reason": entry["migration"]["reason"],
                        "fit_relative_path": entry["fit"]["relative_path"],
                        "fit_sha256": entry["fit"]["sha256"],
                        "fit_size_bytes": entry["fit"]["size_bytes"],
                        "fit_valid": entry["fit"]["valid"],
                    }
                )
        requirements = {
            row["stable_activity_id"]: {
                "source_filename": row["source_filename"],
                "local_start": row.get("source_local_start"),
                "sport": row.get("original_sport"),
                "duration": row.get("source_duration"),
                "distance": row.get("source_distance"),
                "calories": row.get("source_calories"),
                "average_hr": row.get("source_average_hr"),
                "maximum_hr": row.get("source_maximum_hr"),
            }
            for row in rows
            if row["migration_status"] == "requires_configuration"
        }
        template = {
            "config_version": 1,
            "activity_overrides": {
                identifier: {"timezone_offset": None} for identifier in requirements
            },
            "review_requirements": requirements,
        }
        (output / "migration-config.template.json").write_text(
            json.dumps(template, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _write_reports(output: Path, report: dict[str, Any]) -> None:
        (output / "migration-audit.json").write_text(
            json.dumps(report, indent=2, default=dict), encoding="utf-8"
        )
        columns = [
            "source",
            "source_filename",
            "source_id",
            "output_fit",
            "status",
            "failure_stage",
            "error",
            "original_sport",
            "domain_sport",
            "fit_sport",
            "fit_sub_sport",
            "started_at",
            "ended_at",
            "utc_offset_minutes",
            "elapsed_s",
            "timer_s",
            "distance_m",
            "calories",
            "average_hr",
            "maximum_hr",
            "ascent_m",
            "descent_m",
            "fit_bytes",
            "fit_valid",
            "warnings",
        ]
        columns += [
            f"{layer}_{sensor}"
            for layer in ("source", "domain", "fit")
            for sensor in (*SENSORS, "records", "left_crank_power")
        ]
        with (output / "migration-audit.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in report["activities"]:
                flat = {key: row.get(key) for key in columns}
                flat["warnings"] = ";".join(row["warnings"])
                for layer in ("source", "domain", "fit"):
                    for sensor in (*SENSORS, "records", "left_crank_power"):
                        flat[f"{layer}_{sensor}"] = row.get(f"{layer}_counts", {}).get(sensor)
                writer.writerow(flat)
        summary = report["summary"]
        lines = [
            "# Polar migration audit",
            "",
            f"Source: `{report['source_root']}`",
            f"Output: `{report['output_root']}`",
            "",
            "## Totals",
            "",
        ]
        for key in (
            "discovered",
            "parsed",
            "converted",
            "validated",
            "failed",
            "skipped_existing",
            "total_fit_bytes",
            "elapsed_seconds",
            "activities_per_second",
            "earliest",
            "latest",
        ):
            lines.append(f"- {key}: {summary[key]}")
        lines += ["", "## Failure stages", ""]
        for stage, count in summary["failure_stages"].items():
            examples = [row for row in report["activities"] if row.get("failure_stage") == stage][
                :3
            ]
            lines.append(
                f"- {stage}: {count}; examples: "
                + "; ".join(f"`{row['source']}` ({row['error']})" for row in examples)
            )
        lines += ["", "## Failure categories", ""]
        for category, group in summary["failure_categories"].items():
            category_examples = "; ".join(f"`{item['source']}`" for item in group["examples"])
            lines.append(f"- {category}: {group['count']}; examples: {category_examples}")
        lines += [
            "",
            "## Sports",
            "",
            "| Polar sport | Activities | Converted | Failed | Fallback | FIT mapping |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for sport, item in report["sports"].items():
            mapping = ", ".join(item["fit"])
            lines.append(
                f"| {sport} | {item['activities']} | {item['converted']} | {item['failed']} | {item['fallback']} | {mapping} |"
            )
        lines += [
            "",
            "## Sensors",
            "",
            "| Sensor | Source activities / samples | Domain activities / samples | FIT activities / samples |",
            "| --- | ---: | ---: | ---: |",
        ]
        for sensor, item in report["sensors"].items():
            values = [
                f"{item[layer]['activities']} / {item[layer]['samples']}"
                for layer in ("source", "domain", "fit")
            ]
            lines.append(f"| {sensor} | {' | '.join(values)} |")
        lines += ["", "## Warnings", ""]
        lines += [f"- {key}: {value}" for key, value in sorted(summary["warning_counts"].items())]
        lines += [
            "",
            f"Duplicate candidate pairs: {len(report['duplicate_candidates'])}",
            "",
            "No files were uploaded to Strava.",
        ]
        (output / "migration-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
