"""Single-process per-stage timing and cProfile of the audit pipeline (synthetic data)."""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
import tempfile
import tracemalloc
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fit import FITBuilder  # noqa: E402
from polar import PolarImporter  # noqa: E402
from services import audit as audit_module  # noqa: E402
from services.migration import file_sha256, source_sha256  # noqa: E402
from services.validation import ActivityValidator  # noqa: E402


def stages(path: Path, scratch: Path) -> dict[str, float]:
    timings: dict[str, float] = {}

    def step(name: str, function):  # type: ignore[no-untyped-def]
        begin = perf_counter()
        value = function()
        timings[name] = timings.get(name, 0.0) + perf_counter() - begin
        return value

    step("source_sha256", lambda: source_sha256(path))
    step("json_load_only", lambda: json.loads(path.read_text(encoding="utf-8")))
    step("source_info(json+count)", lambda: audit_module._source_info(path))
    step("source_metadata(json)", lambda: audit_module._source_metadata(path))
    activity = step(
        "import_activity(json+parse+laps)", lambda: PolarImporter().import_activity(path)
    )
    step("validate", lambda: ActivityValidator().validate(activity))
    step(
        "domain_counts+warnings",
        lambda: audit_module._warnings(
            activity, audit_module._source_info(path)[1], audit_module._domain_counts(activity)
        ),
    )
    content = step("fit_build(encode+internal_roundtrip)", lambda: FITBuilder().build(activity))
    target = scratch / "x.fit"
    step("fit_write", lambda: target.write_bytes(content))
    step("decode_fit(second_roundtrip)", lambda: audit_module._decode_fit(target, activity))
    step("fit_sha256", lambda: file_sha256(target))
    return timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--profile-top", type=int, default=0, help="print top-N cProfile rows")
    args = parser.parse_args()
    files = []
    for candidate in sorted(PolarImporter().scan(args.source)):
        try:  # skip the deliberate defect files of the synthetic export
            PolarImporter().import_activity(candidate)
        except Exception:
            continue
        files.append(candidate)
        if len(files) == args.limit:
            break
    totals: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as scratch:
        for path in files:
            for name, value in stages(path, Path(scratch)).items():
                totals[name] = totals.get(name, 0.0) + value
        total = sum(totals.values())
        print(f"files={len(files)} bytes={sum(p.stat().st_size for p in files)}")
        for name, value in totals.items():
            print(f"{name:42s} {value / len(files):8.3f} s/file {100 * value / total:5.1f}%")
        print(f"{'sum':42s} {total / len(files):8.3f} s/file")
        tracemalloc.start()
        stages(files[0], Path(scratch))
        print(f"tracemalloc peak (1 file): {tracemalloc.get_traced_memory()[1] / 1e6:.0f} MB")
        tracemalloc.stop()
        if args.profile_top:
            profiler = cProfile.Profile()
            profiler.enable()
            stages(files[0], Path(scratch))
            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(args.profile_top)


if __name__ == "__main__":
    main()
