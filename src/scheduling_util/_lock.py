from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
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
