import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from typing import Iterator

import scheduling_util._lock as lock_module
from scheduling_util._lock import reentrant_exclusive_path_lock


class TestReentrantExclusivePathLock(unittest.TestCase):
    def test_nested_same_thread_uses_one_underlying_lock(self) -> None:
        calls: list[Path] = []
        original_exclusive_path_lock = lock_module.exclusive_path_lock

        @contextmanager
        def fake_exclusive_path_lock(path: Path) -> Iterator[None]:
            calls.append(path)
            yield

        try:
            lock_module.exclusive_path_lock = fake_exclusive_path_lock

            with TemporaryDirectory() as tmp_dir_str:
                path = Path(tmp_dir_str) / "test.lock"

                with lock_module.reentrant_exclusive_path_lock(path):
                    with lock_module.reentrant_exclusive_path_lock(path):
                        pass
        finally:
            lock_module.exclusive_path_lock = original_exclusive_path_lock

        self.assertEqual(1, len(calls))

    def test_nested_same_thread_does_not_deadlock(self) -> None:
        results: Queue[BaseException | str] = Queue()

        def target(path: Path) -> None:
            try:
                with reentrant_exclusive_path_lock(path):
                    with reentrant_exclusive_path_lock(path):
                        results.put("entered")
            except BaseException as e:
                results.put(e)

        with TemporaryDirectory() as tmp_dir_str:
            thread = threading.Thread(
                target=target,
                args=(Path(tmp_dir_str) / "test.lock",),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        result = results.get_nowait()
        if isinstance(result, BaseException):
            raise result

        self.assertEqual("entered", result)
