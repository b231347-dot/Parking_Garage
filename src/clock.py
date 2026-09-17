from datetime import datetime, timedelta
from threading import Lock


class Clock:
    """Thread-safe injectable time source for application behavior and tests."""

    def __init__(self, current_time: datetime | None = None) -> None:
        self._current_time = current_time or datetime.now()
        self._lock = Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._current_time

    def set_time(self, current_time: datetime) -> datetime:
        with self._lock:
            self._current_time = current_time
            return self._current_time

    def advance_hours(self, hours: float) -> datetime:
        with self._lock:
            self._current_time += timedelta(hours=hours)
            return self._current_time
