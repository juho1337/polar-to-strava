# Packaging the engine as a Windows executable

Evaluation for [SPEC-004 AC-12](../specs/004-desktop-integration-contract.md). Goal: end users run
`polar-to-strava-engine.exe` without installing Python. Reproduce with
[`packaging/build_engine.ps1`](../packaging/build_engine.ps1) (isolated venv, builds from a copy of the
sources, PyInstaller 6 one-folder).

```powershell
.\packaging\build_engine.ps1 -Work $env:TEMP\engine-build
```

## Measured (Windows 11, 16 logical CPUs, Python 3.12.10, PyInstaller 6)

| Item | Result |
| --- | --- |
| Output | one folder `polar-to-strava-engine\` (exe 10.3 MB + `_internal`) |
| Size | 45 MB uncompressed |
| Start-up (`version`, 5 runs) | 0.6-0.8 s per process, versus about 0.5 s for `python -m integration` |
| End-to-end through the exe | `audit` of 24 synthetic activities, 4 workers: completed, same events and result as the Python entry; `timezone.list_unresolved` ok |
| Build time | about 3-4 minutes including pip downloads |

Start-up cost is paid by every command, so a host should keep calls coarse (one `audit` or `upload.run`
per operation, not per activity) and use `activity.list`/`status` for UI reads rather than polling in a
tight loop.

## Findings and limitations

- **`multiprocessing.freeze_support()` is mandatory.** Without it the audit's worker processes
  re-launch the whole engine and the pool breaks (`BrokenProcessPool`). It is now the first call in
  `integration.engine.main`. The existing `polar-to-strava` CLI is not frozen; if it ever is, its entry
  point needs the same call.
- **One-folder, not one-file.** `--onefile` unpacks to a temporary directory on every start (slower,
  more antivirus noise) and every audit worker would unpack again; one-folder avoids both.
- **IANA time zones need `tzdata`.** Windows has no system zone database; `tzdata` is a Windows-only
  dependency and the build collects its data. `version` reports `capabilities.timezone_iana`.
- **Unsigned executables** trigger SmartScreen/antivirus heuristics; sign the release build before
  distribution. Frozen Python is a common false-positive source.
- **Not cross-platform.** DPAPI tokens and the `.exe` are Windows-only; the engine itself runs on
  other platforms from source.
- **Size.** 45 MB is dominated by the Python runtime, `lxml`, `pydantic-core` and `fit-tool`'s generated
  profile modules; trimming (excludes, UPX) was not attempted.
- **Updating.** The host should check `version.contract` (major) and `engine` at start-up and refuse an
  incompatible engine.
- **Hosting from .NET:** start the exe with redirected stdin/stdout (UTF-8), write the request line,
  read JSON Lines, send `{"type":"cancel"}` for cancellation and use a Job Object so the audit worker
  processes die with the host.

## Licensing (information, not legal advice)

The project is GPL-3.0-only. A frozen build combines it with its dependencies and the Python runtime:

| Component | License |
| --- | --- |
| polar-to-strava | GPL-3.0-only |
| fit-tool, httpx, lxml | BSD-3-Clause |
| pydantic, PyYAML, rich, typer | MIT |
| tzdata | Apache-2.0 |
| CPython | PSF License |
| PyInstaller (build tool and bootloader) | GPL-2.0-or-later with an exception permitting distribution of any program built with it |

Distributing the executable means distributing a GPL-3.0 program: ship the license text and the
corresponding source (or a written offer) and the third-party notices (`pip-licenses` or the
`_internal\*.dist-info` folders can generate them). A separate desktop host that only starts the engine
as a process and exchanges the documented JSON Lines is generally treated as communicating with a
separate program, not as linking to it, so the host's own license can differ; keep the engine a
separate executable and do not link its Python code into the host. Confirm the exact obligations before
a public release; a private personal build has no distribution obligation.
