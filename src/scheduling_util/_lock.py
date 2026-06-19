from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Iterator, Any


@contextmanager
def exclusive_path_lock(path: Path) -> Iterator[None]:
    """
    Acquires an exclusive lock on a file path.

    Args:
        path: The file path to lock.
    """
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


class _ReentrantPathLockState:
    """
    Tracks in-process ownership for one normalized lock path.

    The underlying file lock is only acquired when `depth` transitions from 0 to 1.
    Nested entries in the same thread increment `depth` and reuse that outer file lock.
    `ref_count` tracks active context managers so unused state can be removed from the module-level registry.
    """

    def __init__(self) -> None:
        """
        Create state for a single normalized lock path.

        `RLock` gives same-thread re-entry while forcing other threads in the
        process to wait until the outermost context exits.
        """
        self.lock = RLock()
        self.depth = 0
        self.ref_count = 0


_reentrant_path_lock_states: dict[Path, _ReentrantPathLockState] = {}
"""
Registry of normalized lock paths to their in-process re-entrant state.
"""


_reentrant_path_lock_states_guard = Lock()
"""
Protects creation, lookup, reference counting, and removal in the registry.
"""


def _normalized_lock_path(path: Path) -> Path:
    """
    Return a stable registry key for a lock path.

    `strict=False` avoids requiring the lock file to exist before it can be used as a key,
    while still resolving relative path components.
    """
    return path.resolve(strict=False)


def _get_reentrant_path_lock_state(path: Path) -> _ReentrantPathLockState:
    """
    Return the shared state object for a normalized lock path.

    The returned state has its reference count incremented for the caller's context manager entry.
    Call `_release_reentrant_path_lock_state` exactly once when that entry is done with the state.
    """
    with _reentrant_path_lock_states_guard:
        try:
            state = _reentrant_path_lock_states[path]
        except KeyError:
            state = _ReentrantPathLockState()
            _reentrant_path_lock_states[path] = state

        state.ref_count += 1
        return state


def _release_reentrant_path_lock_state(
    path: Path,
    state: _ReentrantPathLockState,
) -> None:
    """
    Release one reference to a path's re-entrant lock state.

    When the last active context manager for the path exits, the state is
    removed so the registry does not grow permanently with every path used.
    """
    with _reentrant_path_lock_states_guard:
        state.ref_count -= 1
        if state.ref_count == 0:
            del _reentrant_path_lock_states[path]


@contextmanager
def reentrant_exclusive_path_lock(path: Path) -> Iterator[None]:
    """
    Acquires a same-thread re-entrant exclusive lock on a file path.

    Nested calls for the same normalized path in the same thread reuse the outer file lock
    instead of attempting to acquire a second `flock` lock.

    Other threads in the same process still block on the per-path `RLock`,
    and other processes still block on the underlying `exclusive_path_lock`.
    """
    normalized_path = _normalized_lock_path(path)
    state = _get_reentrant_path_lock_state(normalized_path)

    state.lock.acquire()
    try:
        if state.depth > 0:
            state.depth += 1
            try:
                yield
            finally:
                state.depth -= 1
            return

        state.depth = 1
        try:
            with exclusive_path_lock(path):
                yield
        finally:
            state.depth = 0
    finally:
        state.lock.release()
        _release_reentrant_path_lock_state(normalized_path, state)
