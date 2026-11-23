"""Deterministic synthetic Polar export generator for offline benchmarks.

Produces training-session JSON files shaped like the sanitized fixtures. No private
data is used: all values derive from a seeded pseudo-random generator, so the same
``(count, seed, profile)`` always yields byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROFILES: dict[str, dict[str, int]] = {
    # Minutes of recording per activity (min, max) and sampling interval in seconds.
    "small": {"min_minutes": 5, "max_minutes": 20, "interval": 5},
    "realistic": {"min_minutes": 30, "max_minutes": 180, "interval": 1},
}

# (sport, has_gps, streams, laps?)
KINDS: list[tuple[str, bool, tuple[str, ...], bool]] = [
    ("RUNNING", True, ("heartRate", "speed", "distance", "cadence", "altitude"), True),
    ("CYCLING", True, ("heartRate", "speed", "distance", "cadence", "power", "altitude"), True),
    ("WALKING", True, ("heartRate", "speed", "distance"), False),
    ("TREADMILL_RUNNING", False, ("heartRate", "speed", "distance", "cadence"), False),
    ("STRENGTH_TRAINING", False, ("heartRate",), False),
    ("PADEL", False, ("heartRate", "temperature"), False),
]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _duration(seconds: float) -> str:
    return f"PT{seconds:.3f}S"


def _activity(index: int, rng: random.Random, profile: dict[str, int]) -> dict[str, Any]:
    sport, gps, streams, laps = KINDS[index % len(KINDS)]
    interval = profile["interval"]
    minutes = rng.randint(profile["min_minutes"], profile["max_minutes"])
    seconds = minutes * 60
    start = datetime(2018, 1, 1, 6, 0, 0) + timedelta(hours=index * 7 + rng.randint(0, 3))
    stop = start + timedelta(seconds=seconds)
    count = seconds // interval + 1
    samples: dict[str, Any] = {}
    distance = 0.0
    speed_series: list[float] = []
    for step in range(count):
        speed_series.append(max(0.0, 9.0 + 3.0 * math.sin(step / 90.0) + rng.random()))
    generators: dict[str, Callable[[int], float]] = {
        "heartRate": lambda i: round(110 + 40 * math.sin(i / 200.0) + rng.random() * 5),
        "speed": lambda i: round(speed_series[i], 3),
        "cadence": lambda i: 80 + rng.randint(0, 10),
        "power": lambda i: 150 + rng.randint(0, 120),
        "temperature": lambda i: 18 + rng.randint(0, 2),
        "altitude": lambda i: round(40 + 15 * math.sin(i / 300.0), 2),
    }
    for name in streams:
        if name == "distance":
            values = []
            for step in range(count):
                distance += speed_series[step] / 3.6 * interval
                values.append(round(distance, 2))
            series = values
        else:
            series = [generators[name](step) for step in range(count)]
        samples[name] = [
            {"dateTime": _stamp(start + timedelta(seconds=step * interval)), "value": value}
            for step, value in enumerate(series)
        ]
    if gps:
        latitude, longitude = 60.17 + rng.random() * 0.01, 24.94 + rng.random() * 0.01
        route = []
        for step in range(count):
            latitude += math.cos(step / 500.0) * 1e-5
            longitude += math.sin(step / 500.0) * 1e-5
            route.append(
                {
                    "dateTime": _stamp(start + timedelta(seconds=step * interval)),
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "altitude": round(40 + 15 * math.sin(step / 300.0), 2),
                }
            )
        samples["recordedRoute"] = route
    exercise: dict[str, Any] = {
        "startTime": _stamp(start),
        "stopTime": _stamp(stop),
        "timezoneOffset": 120,
        "duration": _duration(seconds),
        "sport": sport,
        "kiloCalories": minutes * 9,
        "samples": samples,
    }
    if laps:
        # Roughly one lap per kilometre-equivalent; contiguous and covering the exercise.
        lap_seconds = max(interval, 300)
        lap_list, cursor = [], 0
        while cursor < seconds:
            end = min(seconds, cursor + lap_seconds)
            lap_list.append(
                {
                    "startTime": _stamp(start + timedelta(seconds=cursor)),
                    "stopTime": _stamp(start + timedelta(seconds=end)),
                    "distance": round((end - cursor) * 3.0, 1),
                }
            )
            cursor = end
        exercise["laps"] = lap_list
    return {
        "exportVersion": "2.6",
        "name": "",
        "deviceId": f"SYNTH{index % 5}",
        "startTime": _stamp(start),
        "stopTime": _stamp(stop),
        "timeZoneOffset": 120,
        "duration": _duration(seconds),
        "kiloCalories": minutes * 9,
        "exercises": [exercise],
    }


def generate(directory: Path, count: int, seed: int = 1, profile: str = "realistic") -> list[Path]:
    """Write ``count`` activity files (plus a few deliberate defects) into ``directory``."""
    rng = random.Random(seed)
    settings = PROFILES[profile]
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index in range(count):
        payload = _activity(index, rng, settings)
        name = f"training-session-2018-{index:05d}.json"
        path = directory / ("sub" + str(index % 7)) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if index % 97 == 5:  # malformed JSON: exercises the import-failure path
            path.write_text('{"exercises": [', encoding="utf-8")
        elif index % 89 == 7:  # unresolved timezone: requires_configuration
            payload.pop("timeZoneOffset")
            payload["exercises"][0].pop("timezoneOffset")
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="realistic")
    args = parser.parse_args()
    files = generate(args.directory, args.count, args.seed, args.profile)
    total = sum(path.stat().st_size for path in files)
    print(f"{len(files)} files, {total / 1e6:.1f} MB in {args.directory}")


if __name__ == "__main__":
    main()
