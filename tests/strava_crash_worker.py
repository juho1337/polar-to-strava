"""Synthetic subprocess only: terminate without Python cleanup at durable boundaries."""

import os
import sys
from pathlib import Path

from strava.artifacts import VerifiedArtifact
from strava.recovery import AttemptRecord
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock, Pipeline, engine


def main() -> None:
    root, boundary = Path(sys.argv[1]), sys.argv[2]

    def terminate() -> None:
        (root / "crash-boundary.txt").write_text(boundary, encoding="utf-8")
        os._exit(81)

    def checkpoint(stage: str) -> None:
        if boundary == "migration" and stage == "mapped_row":
            terminate()

    with UploadStateStore(root / "migration-state.sqlite3", checkpoint=checkpoint) as store:
        uploader = Uploader(root, store)
        begin = store.begin_submission

        def after_intent(
            identifier: str, expected_revision: int, artifact: VerifiedArtifact, rate_ready: bool
        ) -> AttemptRecord:
            result = begin(identifier, expected_revision, artifact, rate_ready)
            terminate()
            return result

        if boundary == "intent":
            store.begin_submission = after_intent  # type: ignore[method-assign]
        run = engine(root, store, Pipeline(Clock()))
        if boundary == "saved_id":
            run.on_event = lambda event: terminate()
        run.run(uploader.select())
    raise AssertionError("crash boundary not reached")


if __name__ == "__main__":
    main()
