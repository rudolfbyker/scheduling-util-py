import json
from contextlib import contextmanager
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import Generator, Literal, Sequence, Annotated, Union

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, Field

from scheduling_util._lock import reentrant_exclusive_path_lock
from scheduling_util._types import Outcome

logger = getLogger(__name__)


class LastRunHistoryEntryBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str

    at: float
    """
    Unix timestamp in seconds.
    """

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.at)


class LastRunAttemptStarted(LastRunHistoryEntryBase):
    kind: Literal["started"] = "started"


class LastRunAttemptFinished(LastRunHistoryEntryBase):
    kind: Literal["finished"] = "finished"

    outcome: Outcome
    """
    The outcome of the attempt.
    """

    details: str = ""
    """
    Any extra details, e.g. exception message.
    """


LastRunHistoryEntry = Annotated[
    Union[LastRunAttemptStarted, LastRunAttemptFinished], Field(discriminator="kind")
]


class LastRunHistory:
    _entries: list[LastRunHistoryEntry]
    _max_entries: int
    _path: Path | None

    def __init__(
        self,
        *,
        max_entries: int = 1000,
        path: Path | None = None,
        entries: list[LastRunHistoryEntry] | None = None,
    ) -> None:
        """
        Args:
            *:
            max_entries:
                The maximum number of raw history entries to keep.
                A completed attempt normally adds two entries: one "started" entry and one "finished" entry.
            path: The JSON file where the history of attempts is to be stored and read from.
            entries: If given, start with these entries instead of loading from disk.
        """
        if max_entries < 0:
            raise ValueError("max_entries must be non-negative.")

        self._max_entries = max_entries
        self._path = path
        if entries is not None:
            self._entries = entries[:]
        elif path is not None:
            self.load_from_file()
        else:
            self._entries = []

    @property
    def entries(self) -> Sequence[LastRunHistoryEntry]:
        """
        A snapshot of the current list of history entries.
        """
        return self._entries[:]

    def _discard_old_entries(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.pop(0)

    @contextmanager
    def lock(self) -> Generator[None, None, None]:
        """
        A context manager that locks the history entries to prevent race conditions.
        """
        if not self._path:
            yield
            return

        with reentrant_exclusive_path_lock(
            path=self._path.with_suffix(self._path.suffix + ".lock")
        ):
            yield

    def persist_to_file(self) -> None:
        if not self._path:
            return

        with self.lock():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_bytes(
                TypeAdapter(list[LastRunHistoryEntry]).dump_json(
                    self._entries, indent=2
                )
            )

    def load_from_file(self) -> None:
        if not self._path:
            return

        with self.lock():
            self._entries = load_history_entries_from_file(self._path)

    def append(self, entry: LastRunHistoryEntry) -> None:
        with self.lock():
            self._entries.append(entry)
            self._discard_old_entries()
            self.persist_to_file()

    @property
    def n_failures_since_last_success(self) -> int:
        """
        The number of failed attempts since the last successful attempt,
        or since the start of the history if there is no successful attempt.
        """
        with self.lock():
            n = 0
            for entry in reversed(self._entries):
                if not isinstance(entry, LastRunAttemptFinished):
                    continue
                if entry.outcome == "success":
                    break
                if entry.outcome == "failure":
                    n += 1
            return n

    def find_last_entry(
        self,
        *,
        before: datetime | None = None,
        after: datetime | None = None,
        started: Literal[True] | None = None,
        finished: Literal[True] | None = None,
        outcome: Outcome | None = None,
    ) -> LastRunHistoryEntry | None:
        """
        Find the last history entry that matches the given filters.

        Args:
            before: If given, look for a history entry before this time.
            after: If given, look for a history entry after this time.
            started: If given, look for a history entry for a started attempt.
            finished: If given, look for a history entry for a finished attempt.
            outcome: If given, look for a history entry with this outcome. Implies `finished=True`.

        Returns:
            A history entry, or `None`.
        """
        with self.lock():
            if outcome is not None:
                finished = True

            for entry in reversed(self._entries):
                if after is not None and entry.datetime <= after:
                    continue

                if before is not None and entry.datetime >= before:
                    continue

                if started and not isinstance(entry, LastRunAttemptStarted):
                    continue

                if finished:
                    if not isinstance(entry, LastRunAttemptFinished):
                        continue

                    if outcome is not None and entry.outcome != outcome:
                        continue

                return entry

            return None

    @property
    def last_success(self) -> LastRunAttemptFinished | None:
        result = self.find_last_entry(outcome="success")
        if result is None:
            return None
        assert isinstance(result, LastRunAttemptFinished)
        return result

    @property
    def last_neutral(self) -> LastRunAttemptFinished | None:
        result = self.find_last_entry(outcome="neutral")
        if result is None:
            return None
        assert isinstance(result, LastRunAttemptFinished)
        return result

    @property
    def last_failure(self) -> LastRunAttemptFinished | None:
        result = self.find_last_entry(outcome="failure")
        if result is None:
            return None
        assert isinstance(result, LastRunAttemptFinished)
        return result

    @property
    def last_started(self) -> LastRunAttemptStarted | None:
        result = self.find_last_entry(started=True)
        if result is None:
            return None
        assert isinstance(result, LastRunAttemptStarted)
        return result

    @property
    def last_finished(self) -> LastRunAttemptFinished | None:
        result = self.find_last_entry(finished=True)
        if result is None:
            return None
        assert isinstance(result, LastRunAttemptFinished)
        return result


def load_history_entries_from_file(path: Path) -> list[LastRunHistoryEntry]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(
            "Ignoring invalid history file at %s: %s",
            path,
            e,
        )
        return []

    if not isinstance(data, list):
        logger.error(
            "Ignoring invalid history file at %s: expected a list",
            path,
        )
        return []

    entries = []
    for entry_data in data:
        try:
            entry: LastRunHistoryEntry = TypeAdapter(
                LastRunHistoryEntry
            ).validate_python(entry_data)
        except (TypeError, ValueError, ValidationError) as e:
            logger.error(
                "Ignoring invalid entry in history file at %s: %s",
                path,
                e,
            )
            continue
        entries.append(entry)

    return entries
