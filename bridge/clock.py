"""Injected clock seam — makes the ``Re: <name> (YYYY-MM-DD)`` naming deterministic in tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def today(self) -> date:
        """Local calendar date used for the published-doc name."""
        ...

    def now(self) -> datetime:
        """UTC timestamp used for the state file's ``processed_at``."""
        ...


class SystemClock:
    """Production clock — real wall time."""

    def today(self) -> date:
        return datetime.now().date()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Deterministic clock for tests."""

    def __init__(self, day: date, moment: datetime | None = None) -> None:
        self._day = day
        self._moment = moment or datetime(
            day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc
        )

    def today(self) -> date:
        return self._day

    def now(self) -> datetime:
        return self._moment
