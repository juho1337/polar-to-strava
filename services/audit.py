"""Bulk Polar-to-FIT conversion with a per-source migration audit."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from itertools import repeat
from math import isclose
from pathlib import Path
from time import perf_counter
from typing import Any

from fit_tool.fit_file import FitFile  # type: ignore[import-untyped]
from fit_tool.profile.messages.record_message import RecordMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.session_message import SessionMessage  # type: ignore[import-untyped]

from core.contracts import ActivityImporter
from domain import Activity, Sport
from fit import FITBuilder, FITWriter
from fit.builder import SPORTS, fit_time
from services.conversion import output_name
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
            if not isinstance(samples.get("altitude"), list):
                result["altitude"] += sum(
                    isinstance(item, dict) and item.get("altitude") is not None for item in route
                )
        left = samples.get("leftPedalCrankBasedPower")
        if isinstance(left, list):
            result["left_crank_power"] += sum(
                isinstance(item, dict) and item.get("currentPower") is not None for item in left
            )
    return ",".join(sorted(set(sports))) if sports else None, result


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


def _duplicates(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    valid = sorted(
        (row for row in rows if row.get("started_at") and row.get("domain_sport")),
        key=lambda row: row["started_at"],
    )
    pairs: list[dict[str, str]] = []
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
            pairs.append({"first": left["source"], "second": right["source"]})
    return pairs


def _process_one(
    path: Path,
    source: Path,
    fit_directory: Path,
    overwrite: bool,
    importer: ActivityImporter,
    validator: Validator,
) -> dict[str, Any]:
    """Audit a single source in an isolated worker."""
    relative = path.relative_to(source).as_posix()
    target = fit_directory / output_name(path, source, "fit")
    sport, source_counts = _source_info(path)
    row: dict[str, Any] = {
        "source": relative,
        "source_filename": path.name,
        "source_id": path.stem,
        "output_fit": str(target),
        "original_sport": sport,
        "source_counts": source_counts,
        "status": "pending",
        "failure_stage": None,
        "error": None,
        "warnings": [],
    }
    try:
        activity = importer.import_activity(path)
    except Exception as error:
        row.update(
            status="failed",
            failure_stage="import",
            error=str(error),
            error_type=type(error).__name__,
        )
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
        row.update(fit_counts=fit_counts, fit_bytes=size, fit_valid=True)
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

    def run(self, source: Path, output: Path, overwrite: bool = False) -> dict[str, Any]:
        started = perf_counter()
        output.mkdir(parents=True, exist_ok=True)
        fit_directory = output / "fits"
        paths = tuple(self.importer.scan(source))
        with ProcessPoolExecutor(
            max_workers=max(1, min(8, len(paths), os.cpu_count() or 2))
        ) as executor:
            rows = list(
                executor.map(
                    _process_one,
                    paths,
                    repeat(source),
                    repeat(fit_directory),
                    repeat(overwrite),
                    repeat(self.importer),
                    repeat(self.validator),
                )
            )
        statuses = Counter(row["status"] for row in rows)
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
                "warning_counts": dict(warnings),
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
            "duplicate_candidates": _duplicates(rows),
            "activities": rows,
        }
        self._write_reports(output, report)
        return report

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
