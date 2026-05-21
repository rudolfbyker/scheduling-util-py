import os
import unittest
from datetime import timedelta
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Any

from scheduling_util import RateLimiter


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
