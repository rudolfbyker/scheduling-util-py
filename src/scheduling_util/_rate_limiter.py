from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol


@contextmanager
def _exclusive_path_lock(path: Path) -> Iterator[None]:
    if os.name != "posix":
        yield
        return

    import fcntl

    fcntl_module: Any = fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    while True:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
            created = True
            break
        except FileExistsError:
            try:
                fd = os.open(path, os.O_RDWR)
                created = False
                break
            except FileNotFoundError:
                continue

    assert fd is not None
    with os.fdopen(fd, "r+") as lock_file:
        fcntl_module.flock(lock_file.fileno(), fcntl_module.LOCK_EX)
        if created:
            os.utime(path, (0, 0))
        try:
            yield
        finally:
            fcntl_module.flock(lock_file.fileno(), fcntl_module.LOCK_UN)


class RateLimiterProtocol(Protocol):
    """
    Shared interface for rate limiter implementations.
    """

    def ping(self) -> None:
        """
        Record that an action was taken.
        """
        ...

    def ping_if_ready(self) -> bool:
        """
        Record a ping and return True if the action is allowed.
        """
        ...

    def is_ready(self) -> bool:
        """
        Return True if the action is currently allowed.
        """
        ...


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

        On POSIX systems, when `path` is set, the check-and-ping sequence is protected by an advisory file lock
        so that concurrent cooperating processes sharing the same path do not
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
        if self.path is None:
            yield
            return

        with _exclusive_path_lock(self.path):
            yield


@dataclass
class RollingWindowRateLimiter:
    """
    A rate limiter that allows a fixed number of events in a rolling time window.
    """

    max_events: int
    """
    The maximum number of events allowed within the rolling window.
    """

    window: timedelta
    """
    The rolling window in which events are counted.
    """

    path: Path | None = None
    """
    If provided, persist event timestamps to this path.
    """

    timestamps: list[datetime] = field(
        init=False,
        default_factory=list,
        repr=False,
    )
    """
    The timestamps of recent events.
    """

    def __post_init__(self) -> None:
        if self.max_events < 1:
            raise ValueError("max_events must be at least 1")

        if self.window < timedelta(0):
            raise ValueError("window must not be negative")

        if self.path is not None:
            self.path = Path(self.path)
            with self._exclusive_lock():
                self._refresh_timestamps_from_disk()

    def ping(self) -> None:
        """
        Call this when an action was taken.
        """
        with self._exclusive_lock():
            now = datetime.now()
            self._refresh_timestamps_from_disk()
            self._prune_timestamps(now)
            self.timestamps.append(now)
            self._write_timestamps_to_disk()

    def ping_if_ready(self) -> bool:
        """
        Record a ping and return True if the rolling window has capacity.

        On POSIX systems, when `path` is set, the check-and-ping sequence is protected by an advisory file lock
        so that concurrent cooperating processes sharing the same path do not
        all pass the readiness check before one of them persists the ping.
        """
        with self._exclusive_lock():
            now = datetime.now()
            self._refresh_timestamps_from_disk()
            if not self._is_ready_at(now):
                return False

            self.timestamps.append(now)
            self._write_timestamps_to_disk()
            return True

    def is_ready(self) -> bool:
        """
        Check if the rolling window has capacity for another event.
        """
        with self._exclusive_lock():
            self._refresh_timestamps_from_disk()
            return self._is_ready_at(datetime.now())

    def _is_ready_at(self, now: datetime) -> bool:
        self._prune_timestamps(now)
        return len(self.timestamps) < self.max_events

    def _prune_timestamps(self, now: datetime) -> None:
        cutoff = now - self.window
        self.timestamps = [
            timestamp for timestamp in self.timestamps if timestamp > cutoff
        ]

    def _refresh_timestamps_from_disk(self) -> None:
        if self.path is None:
            return

        try:
            raw_state = self.path.read_text()
        except FileNotFoundError:
            self.timestamps = []
            return

        if raw_state.strip() == "":
            self.timestamps = []
            return

        try:
            state = json.loads(raw_state)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid rolling rate limiter state in {self.path}"
            ) from e

        if not isinstance(state, list):
            raise ValueError(f"Invalid rolling rate limiter state in {self.path}")

        try:
            self.timestamps = [datetime.fromtimestamp(float(item)) for item in state]
        except (TypeError, ValueError, OSError, OverflowError) as e:
            raise ValueError(
                f"Invalid rolling rate limiter state in {self.path}"
            ) from e

    def _write_timestamps_to_disk(self) -> None:
        if self.path is None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = [timestamp.timestamp() for timestamp in self.timestamps]
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(json.dumps(state) + "\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, self.path)
            self._fsync_parent_directory()
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    def _fsync_parent_directory(self) -> None:
        if self.path is None or os.name != "posix":
            return

        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _lock_path(self) -> Path:
        assert self.path is not None
        return Path(f"{self.path}.lock")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        if self.path is None:
            yield
            return

        with _exclusive_path_lock(self._lock_path()):
            yield
