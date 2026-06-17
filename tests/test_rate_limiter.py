import json
import os
import unittest
from datetime import timedelta
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
from tempfile import TemporaryDirectory
from time import sleep
from typing import Any

from scheduling_util import RateLimiter, RollingWindowRateLimiter


def _attempt_shared_ping(path: str, barrier: Any, results: Any) -> None:
    try:
        limiter = RateLimiter(
            minimum_period=timedelta(hours=1),
            path=Path(path),
        )
        barrier.wait()
        results.put(limiter.ping_if_ready())
    except BaseException as e:
        results.put(e)


def _attempt_shared_rolling_ping(path: str, barrier: Any, results: Any) -> None:
    try:
        limiter = RollingWindowRateLimiter(
            max_events=3,
            window=timedelta(hours=1),
            path=Path(path),
        )
        barrier.wait()
        results.put(limiter.ping_if_ready())
    except BaseException as e:
        results.put(e)


def _construct_shared_rolling_limiter(
    path: str,
    barrier: Any,
    results: Any,
) -> None:
    try:
        barrier.wait()
        limiter = RollingWindowRateLimiter(
            max_events=1,
            window=timedelta(hours=1),
            path=Path(path),
        )
        results.put(limiter.is_ready())
    except BaseException as e:
        results.put(e)


class TestRateLimiter(unittest.TestCase):
    def test_is_ready_immediately(self) -> None:
        limiter = RateLimiter(minimum_period=timedelta(hours=1))
        self.assertTrue(limiter.is_ready())

    def test_ping_resets_readiness_window(self) -> None:
        limiter = RateLimiter(minimum_period=timedelta(milliseconds=10))

        sleep(0.02)
        self.assertTrue(limiter.is_ready())

        limiter.ping()
        self.assertFalse(limiter.is_ready())

        sleep(0.02)
        self.assertTrue(limiter.is_ready())

    def test_zero_minimum_period_is_always_ready(self) -> None:
        limiter = RateLimiter(minimum_period=timedelta(0))

        limiter.ping()
        self.assertTrue(limiter.is_ready())

    def test_ping_persists_timestamp_to_path(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            limiter1 = RateLimiter(
                minimum_period=timedelta(hours=1),
                path=path,
            )

            self.assertTrue(limiter1.is_ready())
            limiter1.ping()

            self.assertTrue(path.exists())
            limiter2 = RateLimiter(
                minimum_period=timedelta(hours=1),
                path=path,
            )
            self.assertFalse(limiter2.is_ready())

    def test_ping_if_ready_persists_before_returning(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            limiter1 = RateLimiter(
                minimum_period=timedelta(hours=1),
                path=path,
            )

            self.assertTrue(limiter1.ping_if_ready())

            limiter2 = RateLimiter(
                minimum_period=timedelta(hours=1),
                path=path,
            )
            self.assertFalse(limiter2.ping_if_ready())

    @unittest.skipUnless(os.name == "posix", "Linux-only fcntl locking")
    def test_ping_if_ready_allows_only_one_concurrent_process(self) -> None:
        ctx: Any = get_context("fork")

        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            barrier = ctx.Barrier(8)
            results = ctx.Queue()
            processes = [
                ctx.Process(
                    target=_attempt_shared_ping,
                    args=(path.as_posix(), barrier, results),
                )
                for _ in range(8)
            ]

            for process in processes:
                process.start()

            actual = [results.get(timeout=5) for _ in processes]

            for process in processes:
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                self.assertEqual(0, process.exitcode)

        for result in actual:
            self.assertIsInstance(result, bool)

        self.assertEqual(1, actual.count(True))
        self.assertEqual(7, actual.count(False))


class TestRollingWindowRateLimiter(unittest.TestCase):
    def test_allows_events_until_window_capacity_is_reached(self) -> None:
        limiter = RollingWindowRateLimiter(
            max_events=2,
            window=timedelta(hours=1),
        )

        self.assertTrue(limiter.is_ready())
        self.assertTrue(limiter.ping_if_ready())
        self.assertTrue(limiter.is_ready())
        self.assertTrue(limiter.ping_if_ready())
        self.assertFalse(limiter.is_ready())
        self.assertFalse(limiter.ping_if_ready())

    def test_recovers_after_rolling_window_expires(self) -> None:
        limiter = RollingWindowRateLimiter(
            max_events=1,
            window=timedelta(milliseconds=10),
        )

        self.assertTrue(limiter.ping_if_ready())
        self.assertFalse(limiter.is_ready())

        sleep(0.02)
        self.assertTrue(limiter.is_ready())
        self.assertTrue(limiter.ping_if_ready())

    def test_zero_length_window_is_always_ready(self) -> None:
        limiter = RollingWindowRateLimiter(
            max_events=1,
            window=timedelta(0),
        )

        limiter.ping()
        self.assertTrue(limiter.is_ready())

    def test_rejects_non_positive_max_events(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_events"):
            RollingWindowRateLimiter(
                max_events=0,
                window=timedelta(hours=1),
            )

    def test_ping_if_ready_persists_events_to_path(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            limiter1 = RollingWindowRateLimiter(
                max_events=1,
                window=timedelta(hours=1),
                path=path,
            )

            self.assertTrue(limiter1.ping_if_ready())

            limiter2 = RollingWindowRateLimiter(
                max_events=1,
                window=timedelta(hours=1),
                path=path,
            )
            self.assertFalse(limiter2.ping_if_ready())

    def test_ping_if_ready_persists_json_state_to_path(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            limiter = RollingWindowRateLimiter(
                max_events=1,
                window=timedelta(hours=1),
                path=path,
            )

            self.assertTrue(limiter.ping_if_ready())

            self.assertEqual(1, len(json.loads(path.read_text())))
            self.assertEqual([], list(path.parent.glob(f".{path.name}.*.tmp")))

    @unittest.skipUnless(os.name == "posix", "Linux-only fcntl locking")
    def test_constructor_read_waits_for_shared_lock(self) -> None:
        import fcntl

        ctx: Any = get_context("fork")

        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            lock_path = Path(f"{path}.lock")
            path.write_text("[")
            lock_path.touch()

            fd = os.open(lock_path, os.O_RDWR)
            with os.fdopen(fd, "r+") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

                barrier = ctx.Barrier(2)
                results = ctx.Queue()
                process = ctx.Process(
                    target=_construct_shared_rolling_limiter,
                    args=(path.as_posix(), barrier, results),
                )
                process.start()
                barrier.wait()

                with self.assertRaises(Empty):
                    results.get(timeout=0.2)

                path.write_text("[]\n")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

                result = results.get(timeout=5)
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                self.assertEqual(0, process.exitcode)

        self.assertIs(result, True)

    @unittest.skipUnless(os.name == "posix", "Linux-only fcntl locking")
    def test_ping_if_ready_allows_limited_concurrent_processes(self) -> None:
        ctx: Any = get_context("fork")

        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "limiter"
            barrier = ctx.Barrier(8)
            results = ctx.Queue()
            processes = [
                ctx.Process(
                    target=_attempt_shared_rolling_ping,
                    args=(path.as_posix(), barrier, results),
                )
                for _ in range(8)
            ]

            for process in processes:
                process.start()

            actual = [results.get(timeout=5) for _ in processes]

            for process in processes:
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                self.assertEqual(0, process.exitcode)

        for result in actual:
            self.assertIsInstance(result, bool)

        self.assertEqual(3, actual.count(True))
        self.assertEqual(5, actual.count(False))
