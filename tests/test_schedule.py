import re
import unittest
from datetime import timedelta
from logging import DEBUG
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Any, Callable
from unittest.mock import patch

import requests_mock
from comparable_pattern import ComparablePattern

from scheduling_util import RateLimiter, schedule, repeat
from scheduling_util._last_run import read_state
from .util import any_uuid


class TestScheduleFunction(unittest.TestCase):
    def _slack_rate_limiter(self, tmp_dir: Path) -> RateLimiter:
        return RateLimiter(
            minimum_period=timedelta(hours=1),
            path=tmp_dir / "rate_limiter" / "slack_errors",
        )

    def test_success_on_first_try(self) -> None:

        n = 0

        def count() -> None:
            nonlocal n
            n += 1

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            heartbeat_file = tmp_dir / "heartbeat.txt"

            m.register_uri(
                method=requests_mock.ANY,
                url=requests_mock.ANY,
                status_code=200,
                text="OK",
            )

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=3,
                heartbeat_file=heartbeat_file,
                slug="test",
                description="Test schedule",
                last_run_dir=tmp_dir / "last-run",
                success_period=timedelta(days=1),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=count,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

        self.assertEqual(n, 1)
        self.assertEqual(
            [
                "GET https://healthchecks.io/api/v3/checks?slug=test 200",
                "Updating heartbeat …",
                "[test] Checking if it should run …",
                "[test] Started.",
                "Entering context for health check with slug=test",
                "Sending start ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test/start?create=1&rid="
                + any_uuid
                + " 200",
                "Context for health check with slug=test exited without exception.",
                "Sending success ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test?create=1&rid="
                + any_uuid
                + " 200",
                "[test] Succeeded.",
                "Sleeping for 0:00:00.001000 …",
                "Updating heartbeat …",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "Sleeping for 0:00:00.001000 …",
                "Updating heartbeat …",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "Done.",
            ],
            [r.message for r in logs.records],
        )

    def test_retry_after_failure_period_elapsed(self) -> None:

        n = 0

        def fail_once_then_succeed() -> None:
            nonlocal n
            n += 1
            if n == 1:
                raise RuntimeError("boom")

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            m.register_uri(
                method=requests_mock.ANY,
                url=requests_mock.ANY,
                status_code=200,
                text="OK",
            )

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_file=None,
                slug="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                success_period=timedelta(days=1),
                failure_period=timedelta(milliseconds=0),
                max_failures=3,
                on_max_failures="stall",
                func=fail_once_then_succeed,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = read_state(path=last_run_dir / "test.json")

        self.assertEqual(2, n)
        self.assertEqual(0, state["n_consecutive_failures"])
        self.assertIsNotNone(state["last_attempted"])
        self.assertIsNotNone(state["last_failed"])
        self.assertIsNotNone(state["last_successful"])
        self.assertEqual("boom", state["last_failure"])

        self.assertEqual(
            [
                "GET https://healthchecks.io/api/v3/checks?slug=test 200",
                "[test] Checking if it should run …",
                "[test] Started.",
                "Entering context for health check with slug=test",
                "Sending start ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test/start?create=1&rid="
                + any_uuid
                + " 200",
                """\
Context for health check with slug=test exited with exception `RuntimeError`:
boom""",
                f"""\
Sending logs for health check with slug=test:
Exception type: RuntimeError

Exception details:
  boom

Traceback:"""
                + ComparablePattern(re.compile(r".*")),
                "POST https://hc-ping.com/ping-key/test/log?create=1&rid="
                + any_uuid
                + " 200",
                "Sending failure ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test/fail?create=1&rid="
                + any_uuid
                + " 200",
                "[test] Failed: boom",
                "boom",
                "Sleeping for 0:00:00.001000 …",
                "[test] Checking if it should run …",
                "[test] Started.",
                "Entering context for health check with slug=test",
                "Sending start ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test/start?create=1&rid="
                + any_uuid
                + " 200",
                "Context for health check with slug=test exited without exception.",
                "Sending success ping for health check with slug=test.",
                "GET https://hc-ping.com/ping-key/test?create=1&rid="
                + any_uuid
                + " 200",
                "[test] Succeeded.",
                "Done.",
            ],
            [r.message for r in logs.records],
        )

    def test_stall_after_max_failures(self) -> None:

        n = 0

        def fail() -> None:
            nonlocal n
            n += 1
            raise RuntimeError("boom")

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            m.register_uri(
                method=requests_mock.ANY,
                url=requests_mock.ANY,
                status_code=200,
                text="OK",
            )

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_file=None,
                slug="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                success_period=timedelta(days=1),
                failure_period=timedelta(milliseconds=0),
                max_failures=1,
                on_max_failures="stall",
                func=fail,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = read_state(path=last_run_dir / "test.json")

        self.assertEqual(1, n)
        self.assertEqual(1, state["n_consecutive_failures"])
        self.assertIsNotNone(state["last_failed"])
        self.assertIsNone(state["last_successful"])
        self.assertEqual("boom", state["last_failure"])

        messages = [r.message for r in logs.records]
        self.assertEqual(1, messages.count("[test] Started."))
        self.assertIn(
            """\
Job stalled after 1 tries:
boom""",
            messages,
        )
        self.assertIn("[test] Skipped.", messages)
        self.assertIn("Done.", messages)

    def test_keyboard_interrupt_stops_scheduler(self) -> None:

        n = 0

        def interrupt() -> None:
            nonlocal n
            n += 1
            raise KeyboardInterrupt()

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            m.register_uri(
                method=requests_mock.ANY,
                url=requests_mock.ANY,
                status_code=200,
                text="OK",
            )

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=3,
                heartbeat_file=None,
                slug="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                success_period=timedelta(days=1),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=interrupt,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = read_state(path=last_run_dir / "test.json")

        self.assertEqual(1, n)
        self.assertEqual(1, state["n_consecutive_failures"])
        self.assertIsNotNone(state["last_failed"])

        messages = [r.message for r in logs.records]
        self.assertEqual(1, messages.count("[test] Started."))
        self.assertIn("Received KeyboardInterrupt, stopping …", messages)
        self.assertIn("Done.", messages)
        self.assertNotIn("Sleeping for 0:00:00.001000 …", messages)


class TestRepeatFunction(unittest.TestCase):
    def _test(
        self,
        max_runs: int | None,
        sleep_timedelta: timedelta | None,
        error_callback: Callable[[int], Exception | None] | None = None,
    ) -> List[str]:
        events: List[str] = []

        def func() -> None:
            events.append("func()")
            if error_callback is not None:
                exception = error_callback(len(events))
                if exception is not None:
                    raise exception

        def mock_sleep(timedelta: Any) -> None:
            events.append(f"sleep({timedelta})")

        try:
            with patch(
                target="scheduling_util._schedule.sleep",
                side_effect=mock_sleep,
            ):
                repeat(
                    func=func,
                    max_runs=max_runs,
                    sleep_duration=sleep_timedelta,
                )
        except Exception as e:
            events.append(f"{type(e).__name__}: {e}")

        return events

    def test_0_runs(self) -> None:
        self.assertEqual(
            [],
            self._test(
                max_runs=0,
                sleep_timedelta=timedelta(milliseconds=1),
            ),
        )

    def test_1_run(self) -> None:
        self.assertEqual(
            ["func()"],
            self._test(
                max_runs=1,
                sleep_timedelta=timedelta(milliseconds=1),
            ),
        )

    def test_2_runs(self) -> None:
        self.assertEqual(
            ["func()", "sleep(0.001)", "func()"],
            self._test(
                max_runs=2,
                sleep_timedelta=timedelta(milliseconds=1),
            ),
        )

    def test_3_runs(self) -> None:
        self.assertEqual(
            ["func()", "sleep(0.001)", "func()", "sleep(0.001)", "func()"],
            self._test(
                max_runs=3,
                sleep_timedelta=timedelta(milliseconds=1),
            ),
        )

    def test_3_runs__zero_sleep(self) -> None:
        self.assertEqual(
            ["func()", "func()", "func()"],
            self._test(
                max_runs=3,
                sleep_timedelta=timedelta(milliseconds=0),
            ),
        )

    def test_3_runs__no_sleep(self) -> None:
        self.assertEqual(
            ["func()", "func()", "func()"],
            self._test(
                max_runs=3,
                sleep_timedelta=None,
            ),
        )

    def test_indefinite_runs(self) -> None:
        def error_callback(n: int) -> RuntimeError | None:
            if n > 4:
                return RuntimeError("stop")

            return None

        self.assertEqual(
            [
                "func()",
                "sleep(0.001)",
                "func()",
                "sleep(0.001)",
                "func()",
                "RuntimeError: stop",
            ],
            self._test(
                max_runs=None,
                sleep_timedelta=timedelta(milliseconds=1),
                error_callback=error_callback,
            ),
        )
