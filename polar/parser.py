"""Parser for one Polar Flow activity JSON file."""

import json
from pathlib import Path
from pydantic import ValidationError
from polar.models import PolarActivity


class PolarParseError(ValueError):
    """Raised when an activity file is unreadable or invalid."""


def parse_activity(path: Path | str) -> PolarActivity:
    """Load and validate one Polar activity JSON document."""
    activity_path = Path(path)
    try:
        with activity_path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise PolarParseError(f"Could not read {activity_path}: {error}") from error
    try:
        return PolarActivity.model_validate(payload)
    except ValidationError as error:
        raise PolarParseError(f"Invalid Polar activity in {activity_path}: {error}") from error
