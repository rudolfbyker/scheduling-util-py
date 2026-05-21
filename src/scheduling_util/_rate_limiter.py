from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


@dataclass
class RateLimiter:
    """
    A simple timestamp-based rate limiter with optional process-safe persistence to disk.
    """

    minimum_period: timedelta
    """
    The minimum amount of time to wait between actions.
    """

    path: Path | None = None
    """
    If provided, persist the timestamp to this path's modification time.
    """

    timestamp: datetime = field(
        init=False,
        default=datetime.fromtimestamp(0),
        repr=False,
    )
    """
    The time of the last action.
    """

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self._refresh_timestamp_from_disk()

    def ping(self) -> None:
        """
        Call this when an action was taken.
        """
        with self._exclusive_lock():
            self._touch_persistent_timestamp()

    def ping_if_ready(self) -> bool:
        """
        Record a ping and return True if enough time has passed.

        When `path` is set, the check-and-ping sequence is protected by a
        Linux file lock so concurrent processes sharing the same path do not
        all pass the readiness check before one of them persists the ping.
        """
        with self._exclusive_lock():
            self._refresh_timestamp_from_disk()
            if not self._is_ready_at(datetime.now()):
                return False

            self._touch_persistent_timestamp()
            return True

    def is_ready(self) -> bool:
        """
        Check if enough time has passed since the last action.
        """
        self._refresh_timestamp_from_disk()
        return self._is_ready_at(datetime.now())

    def _is_ready_at(self, now: datetime) -> bool:
        return now - self.timestamp >= self.minimum_period

    def _refresh_timestamp_from_disk(self) -> None:
        if self.path is None:
            return

        try:
            self.timestamp = datetime.fromtimestamp(self.path.stat().st_mtime)
        except FileNotFoundError:
            self.timestamp = datetime.fromtimestamp(0)

    def _touch_persistent_timestamp(self) -> None:
        if self.path is None:
            self.timestamp = datetime.now()
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()
        self._refresh_timestamp_from_disk()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        if self.path is None or os.name != "posix":
            yield
            return

        import fcntl

        fcntl_module: Any = fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        while True:
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                created = True
                break
            except FileExistsError:
                try:
                    fd = os.open(self.path, os.O_RDWR)
                    created = False
                    break
                except FileNotFoundError:
                    continue

        assert fd is not None
        with os.fdopen(fd, "r+") as lock_file:
            fcntl_module.flock(lock_file.fileno(), fcntl_module.LOCK_EX)
            if created:
                os.utime(self.path, (0, 0))
            try:
                yield
            finally:
                fcntl_module.flock(lock_file.fileno(), fcntl_module.LOCK_UN)
