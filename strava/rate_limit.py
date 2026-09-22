"""Header-driven Strava API rate-limit policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from strava.models import RateLimit


class DailyLimitReached(Exception):
    """The configured safety reserve would be consumed before midnight UTC."""


@dataclass(frozen=True, slots=True)
class RateWait:
    seconds: float
    resume_at: datetime


class RateLimitPolicy:
    def __init__(
        self,
        reserve: int = 10,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None],
        on_wait: Callable[[RateWait], None] | None = None,
    ) -> None:
        if reserve < 0:
            raise ValueError("rate-limit reserve cannot be negative")
        self.reserve = reserve
        self.clock = clock
        self.sleep = sleep
        self.on_wait = on_wait

    def before_request(self, rate: RateLimit | None, *, read: bool = False) -> bool:
        if rate is None:
            return False
        daily_remaining = rate.daily_limit - rate.daily_usage
        short_remaining = rate.short_limit - rate.short_usage
        if read and rate.read_daily_limit is not None and rate.read_daily_usage is not None:
            daily_remaining = min(daily_remaining, rate.read_daily_limit - rate.read_daily_usage)
        if read and rate.read_short_limit is not None and rate.read_short_usage is not None:
            short_remaining = min(short_remaining, rate.read_short_limit - rate.read_short_usage)
        if daily_remaining <= self.reserve:
            now = self.clock()
            reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            raise DailyLimitReached(
                f"daily API safety reserve reached; resumes after midnight UTC "
                f"({reset.isoformat()})"
            )
        if short_remaining > self.reserve:
            return False
        now = self.clock()
        reset_minute = ((now.minute // 15) + 1) * 15
        reset = now.replace(second=0, microsecond=0)
        if reset_minute == 60:
            reset = reset.replace(minute=0) + timedelta(hours=1)
        else:
            reset = reset.replace(minute=reset_minute)
        wait = max(1.0, (reset - now).total_seconds() + 1.0)
        notice = RateWait(wait, reset + timedelta(seconds=1))
        if self.on_wait:
            self.on_wait(notice)
        self.sleep(wait)
        return True
