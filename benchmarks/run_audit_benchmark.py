"""Repeatable offline audit benchmark: cold/warm runs with process-tree accounting.

Each run executes ``main.py audit`` in a fresh subprocess (so interpreter start-up and
worker spawning are included) and reports wall time, CPU time, peak memory and disk I/O
for the whole process tree. On Windows the tree is measured with a Job Object; elsewhere
``resource.getrusage(RUSAGE_CHILDREN)`` is used (no I/O counters).

* cold  = empty output directory (all FIT files generated)
* warm  = rerun into the same output directory (existing FIT files are re-validated)

Only synthetic data (see ``synthetic_export.py``) or an explicitly approved directory
should be used. Results contain no paths from the data set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# Files whose content must be identical between runs/versions (timing fields excluded).
EQUIVALENCE_FILES = (
    "migration-manifest.json",
    "migration-manifest.csv",
    "migration-config.template.json",
)


def _run_windows(command: list[str]) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")
        ]

    class BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class BasicAndIo(ctypes.Structure):
        _fields_ = [("Basic", BasicAccounting), ("Io", IoCounters)]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    start = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):  # type: ignore[attr-defined]
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    code = process.wait()
    wall = time.perf_counter() - start
    basic = BasicAndIo()
    kernel32.QueryInformationJobObject(job, 8, ctypes.byref(basic), ctypes.sizeof(basic), None)
    extended = ExtendedLimit()
    kernel32.QueryInformationJobObject(
        job, 9, ctypes.byref(extended), ctypes.sizeof(extended), None
    )
    ticks = 1e-7  # 100 ns units
    return {
        "exit_code": code,
        "wall_s": wall,
        "cpu_user_s": basic.Basic.TotalUserTime * ticks,
        "cpu_kernel_s": basic.Basic.TotalKernelTime * ticks,
        "processes": basic.Basic.TotalProcesses,
        "peak_job_memory_mb": extended.PeakJobMemoryUsed / 1e6,
        "io_read_mb": basic.Io.ReadBytes / 1e6,
        "io_write_mb": basic.Io.WriteBytes / 1e6,
    }


def _run_posix(command: list[str]) -> dict[str, Any]:
    import resource

    start = time.perf_counter()
    code = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    wall = time.perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)  # type: ignore[attr-defined,unused-ignore]
    return {
        "exit_code": code,
        "wall_s": wall,
        "cpu_user_s": usage.ru_utime,
        "cpu_kernel_s": usage.ru_stime,
        "processes": None,
        "peak_job_memory_mb": usage.ru_maxrss / 1024,
        "io_read_mb": None,
        "io_write_mb": None,
    }


def measure(command: list[str]) -> dict[str, Any]:
    return _run_windows(command) if sys.platform == "win32" else _run_posix(command)


def fingerprint(output: Path) -> dict[str, str]:
    """Hashes of deterministic outputs: manifests plus every generated FIT file."""
    result = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in EQUIVALENCE_FILES
    }
    fits = hashlib.sha256()
    for fit in sorted((output / "fits").glob("*.fit")):
        fits.update(fit.name.encode() + b"\0" + fit.read_bytes())
    result["fits"] = fits.hexdigest()
    report = json.loads((output / "migration-audit.json").read_text(encoding="utf-8"))
    for row in report["activities"]:  # timing-independent semantic projection
        row.pop("output_fit", None)
    summary = report["summary"]
    summary.pop("elapsed_seconds", None)
    summary.pop("activities_per_second", None)
    report.pop("output_root", None)
    report.pop("source_root", None)
    result["audit_semantic"] = hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()
    ).hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--work", type=Path, required=True, help="scratch output parent directory")
    parser.add_argument("--label", default="run")
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument("--json", type=Path, help="write results to this file")
    parser.add_argument("audit_args", nargs="*", help="extra `audit` arguments, after --")
    args = parser.parse_args()
    count = sum(1 for _ in args.source.rglob("training-session-*.json"))
    output = args.work / f"out-{args.label}"
    shutil.rmtree(output, ignore_errors=True)
    command = [
        sys.executable,
        str(ROOT / "main.py"),
        "audit",
        str(args.source),
        "-o",
        str(output),
        *args.audit_args,
    ]
    runs = []
    for name in ["cold"] + [f"warm{i + 1}" for i in range(args.warm_runs)]:
        result = measure(command)
        result.update(run=name, activities=count, activities_per_second=count / result["wall_s"])
        result["fingerprint"] = fingerprint(output) if output.exists() else None
        runs.append(result)
        print(
            json.dumps(
                {
                    k: (round(v, 2) if isinstance(v, float) else v)
                    for k, v in result.items()
                    if k != "fingerprint"
                }
            )
        )
    summary = {
        "label": args.label,
        "activities": count,
        "python": sys.version.split()[0],
        "runs": runs,
    }
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
