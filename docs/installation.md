# Installation

Polar Activity Migrator requires Python 3.12 or newer. The project is packaged with
`pyproject.toml`; `pip install -e .` installs the application and its runtime dependencies.
The optional `dev` extra adds the test and code-quality tools. `requirements.txt` mirrors
the development dependency set but is not the installation method used by this guide.

## Windows PowerShell

Install [Python](https://www.python.org/downloads/) and
[Git](https://git-scm.com/downloads/), then open PowerShell:

```powershell
git clone https://github.com/juho1337/polar-to-strava.git
cd polar-to-strava
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python main.py --help
```

If PowerShell blocks activation, you can use the environment without activating it:

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -e .
& '.\.venv\Scripts\python.exe' main.py --help
```

## macOS and Linux

```bash
git clone https://github.com/juho1337/polar-to-strava.git
cd polar-to-strava
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python main.py --help
```

The help output should list `audit`, `scan`, `convert`, `inspect`, and `strava`.
Installation also creates the equivalent `polar-to-strava` console command.

## Verify development tools

Contributors can add the development dependencies before running these checks:

```text
python -m pip install -e ".[dev]"
```

```text
pytest
ruff check .
black --check .
mypy .
```

The project has been exercised most extensively on Windows. Its paths and CLI use Python
cross-platform APIs, but real migration work should begin with an audit and small dry run
on your own system.
