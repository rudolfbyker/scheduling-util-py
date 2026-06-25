import json
import re
import unittest
from datetime import timedelta
from logging import DEBUG
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Any, Callable, Literal, Never
from unittest.mock import patch

import requests_mock
from comparable_pattern import ComparablePattern

from scheduling_util import RateLimiter, schedule, repeat
from .util import any_uuid, AnyFloat

health_check_uuid = "00000000-0000-0000-0000-000000000001"


class TestScheduleFunction(unittest.TestCase):
    def _slack_rate_limiter(self, tmp_dir: Path) -> RateLimiter:
        return RateLimiter(
            minimum_period=timedelta(hours=1),
            path=tmp_dir / "rate_limiter" / "slack_errors",
        )

    def _mock_healthchecks(self, m: Any) -> None:
        m.register_uri(
            method=requests_mock.ANY,
            url=requests_mock.ANY,
            status_code=200,
            text="OK",
        )
        m.register_uri(
            method="POST",
            url="https://healthchecks.io/api/v3/checks/",
            status_code=200,
            json={"uuid": health_check_uuid},
        )

    def test_success_on_first_try(self) -> None:

        n = 0

        def count() -> Literal["success"]:
            nonlocal n
            n += 1
            return "success"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            heartbeat_path = tmp_dir / "heartbeat.txt"

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=3,
                heartbeat_path=heartbeat_path,
                name="test",
                description="Test schedule",
                last_run_dir=tmp_dir / "last-run",
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=count,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            create_request = next(r for r in m.request_history if r.method == "POST" and r.url == "https://healthchecks.io/api/v3/checks/")

        self.assertEqual(n, 1)
        self.assertEqual("POST", create_request.method)
        self.assertEqual("https://healthchecks.io/api/v3/checks/", create_request.url)
        self.assertEqual(
            {
                "name": "test",
                "slug": "test",
                "desc": "Test schedule",
                "timeout": 86400,
                "grace": 172800,
                "unique": ["slug"],
            },
            create_request.json(),
        )
        self.assertEqual(
            [
                "POST https://healthchecks.io/api/v3/checks/ 200",
                "Updating heartbeat …",
                "[test] Checking if it should run …",
                "[test] Started.",
                f"Entering context for health check with slug=test and uuid={health_check_uuid}",
                f"Sending start ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}/start?rid="
                + any_uuid
                + " 200",
                "Result for `test` is `success`.",
                f"Sending success ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}?rid=" + any_uuid + " 200",
                f"Context for health check with slug=test and uuid={health_check_uuid} exited without exception.",
                f"Ping already sent for health check with slug=test and uuid={health_check_uuid}",
                "[test] Returned `success`.",
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

    def test_ping_key_without_manage_key_autoprovisions_by_slug(self) -> None:
        n = 0

        def count() -> Literal["success"]:
            nonlocal n
            n += 1
            return "success"

        with (
            TemporaryDirectory() as tmp_dir_str,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            m.register_uri(
                method=requests_mock.ANY,
                url=requests_mock.ANY,
                status_code=200,
                text="OK",
            )

            schedule(
                hc_ping_key="ping-key",
                interval=timedelta(milliseconds=1),
                max_runs=1,
                heartbeat_path=None,
                name="test",
                description=None,
                last_run_dir=tmp_dir / "last-run",
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=count,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            requested_urls = [request.url for request in m.request_history]

        self.assertEqual(n, 1)
        self.assertEqual(
            [
                "https://hc-ping.com/ping-key/test/start?create=1&rid=" + any_uuid,
                "https://hc-ping.com/ping-key/test?create=1&rid=" + any_uuid,
            ],
            requested_urls,
        )

    def test_retry_after_failure_period_elapsed(self) -> None:

        n = 0

        def raise_once_then_succeed() -> Literal["success"]:
            nonlocal n
            n += 1
            if n == 1:
                raise RuntimeError("boom")
            return "success"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(milliseconds=0),
                max_failures=3,
                on_max_failures="stall",
                func=raise_once_then_succeed,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(2, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Raised `RuntimeError`: boom",
                    "kind": "finished",
                    "outcome": "failure",
                },
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "",
                    "kind": "finished",
                    "outcome": "success",
                },
            ],
            state,
        )

        self.assertEqual(
            [
                "POST https://healthchecks.io/api/v3/checks/ 200",
                "[test] Checking if it should run …",
                "[test] Started.",
                f"Entering context for health check with slug=test and uuid={health_check_uuid}",
                f"Sending start ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}/start?rid="
                + any_uuid
                + " 200",
                """\
Context for health check with slug=test and uuid=00000000-0000-0000-0000-000000000001 exited with exception `RuntimeError`:
boom""",
                f"""\
Sending logs for health check with slug=test and uuid={health_check_uuid}:
Exception type: RuntimeError

Exception details:
  boom

Traceback:""" + ComparablePattern(re.compile(r".*")),
                f"POST https://hc-ping.com/{health_check_uuid}/log?rid="
                + any_uuid
                + " 200",
                f"Sending failure ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}/fail?rid="
                + any_uuid
                + " 200",
                "[test] Raised `RuntimeError`: boom",
                "RuntimeError: boom",
                "Sleeping for 0:00:00.001000 …",
                "[test] Checking if it should run …",
                "[test] Started.",
                f"Entering context for health check with slug=test and uuid={health_check_uuid}",
                f"Sending start ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}/start?rid="
                + any_uuid
                + " 200",
                "Result for `test` is `success`.",
                f"Sending success ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}?rid=" + any_uuid + " 200",
                f"Context for health check with slug=test and uuid={health_check_uuid} exited without exception.",
                f"Ping already sent for health check with slug=test and uuid={health_check_uuid}",
                "[test] Returned `success`.",
                "Done.",
            ],
            [r.message for r in logs.records],
        )

    def test_failure_result_notifies_healthchecks_but_not_slack(self) -> None:
        n = 0
        slack_webhook = "https://hooks.slack.test/example"

        def return_failure() -> Literal["failure"]:
            nonlocal n
            n += 1
            return "failure"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(days=1),
                max_failures=3,
                on_max_failures="stall",
                func=return_failure,
                slack_webhook=slack_webhook,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())
            requested_urls = [request.url for request in m.request_history]

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "",
                    "kind": "finished",
                    "outcome": "failure",
                },
            ],
            state,
        )

        self.assertEqual(
            [
                "https://healthchecks.io/api/v3/checks/",
                "https://hc-ping.com/00000000-0000-0000-0000-000000000001/start?rid="
                + any_uuid,
                "https://hc-ping.com/00000000-0000-0000-0000-000000000001/fail?rid="
                + any_uuid,
            ],
            requested_urls,
        )

        messages = [r.message for r in logs.records]
        self.assertIn("Result for `test` is `failure`.", messages)
        self.assertIn(
            f"Sending failure ping for health check with slug=test and uuid={health_check_uuid}.",
            messages,
        )
        self.assertNotIn("Posting error to Slack.", messages)
        self.assertNotIn("RuntimeError: boom", messages)

    def test_none_result_without_healthcheck_is_success_for_backward_compatibility(
        self,
    ) -> None:
        n = 0

        def return_none() -> None:
            nonlocal n
            n += 1
            return None

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            schedule(
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_path=None,
                name="test",
                description=None,
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(0),
                max_failures=3,
                on_max_failures="stall",
                func=return_none,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "",
                    "kind": "finished",
                    "outcome": "success",
                },
            ],
            state,
        )

        messages = [r.message for r in logs.records]
        self.assertNotIn("Unexpected result from `test`: `None`", messages)
        self.assertNotIn(
            f"Sending failure ping for health check with slug=test and uuid={health_check_uuid}.",
            messages,
        )

    def test_unexpected_non_none_result_without_healthcheck_is_failure_and_retries(
        self,
    ) -> None:
        n = 0

        def return_unknown() -> Any:
            nonlocal n
            n += 1
            return "unknown"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            schedule(
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_path=None,
                name="test",
                description=None,
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(0),
                max_failures=3,
                on_max_failures="stall",
                func=return_unknown,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(2, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Unexpected result from `test`: `unknown`",
                    "kind": "finished",
                    "outcome": "failure",
                },
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Unexpected result from `test`: `unknown`",
                    "kind": "finished",
                    "outcome": "failure",
                },
            ],
            state,
        )

        messages = [r.message for r in logs.records]
        self.assertEqual(
            2,
            messages.count("Unexpected result from `test`: `unknown`"),
        )
        self.assertNotIn(
            f"Sending failure ping for health check with slug=test and uuid={health_check_uuid}.",
            messages,
        )

    def test_unexpected_non_none_result_notifies_healthchecks_as_failure(self) -> None:
        n = 0

        def return_unknown() -> Any:
            nonlocal n
            n += 1
            return "unknown"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=1,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=return_unknown,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())
            requested_urls = [request.url for request in m.request_history]

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Unexpected result from `test`: `unknown`",
                    "kind": "finished",
                    "outcome": "failure",
                },
            ],
            state,
        )

        self.assertEqual(
            [
                "https://healthchecks.io/api/v3/checks/",
                "https://hc-ping.com/00000000-0000-0000-0000-000000000001/start?rid="
                + any_uuid,
                "https://hc-ping.com/00000000-0000-0000-0000-000000000001/fail?rid="
                + any_uuid,
            ],
            requested_urls,
        )

        messages = [r.message for r in logs.records]
        self.assertIn("Unexpected result from `test`: `unknown`", messages)
        self.assertIn(
            f"Sending failure ping for health check with slug=test and uuid={health_check_uuid}.",
            messages,
        )

    def test_stall_after_max_failures(self) -> None:

        n = 0

        def fail() -> Never:
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

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=2,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(milliseconds=0),
                max_failures=1,
                on_max_failures="stall",
                func=fail,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Raised `RuntimeError`: boom",
                    "kind": "finished",
                    "outcome": "failure",
                },
            ],
            state,
        )

        messages = [r.message for r in logs.records]
        self.assertEqual(1, messages.count("[test] Started."))
        stall_messages = [
            message
            for message in messages
            if message.startswith("Job stalled after 1 tries. The last failure was:\n{")
        ]
        self.assertEqual(1, len(stall_messages))
        self.assertIn('"kind": "finished"', stall_messages[0])
        self.assertIn('"outcome": "failure"', stall_messages[0])
        self.assertIn(
            '"details": "Raised `RuntimeError`: boom"',
            stall_messages[0],
        )
        self.assertIn("[test] Skipped.", messages)
        self.assertIn("Done.", messages)

    def test_keyboard_interrupt_stops_scheduler(self) -> None:

        n = 0

        def interrupt() -> Never:
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

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=3,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=interrupt,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "Raised `KeyboardInterrupt`: ",
                    "kind": "finished",
                    "outcome": "failure",
                },
            ],
            state,
        )

        messages = [r.message for r in logs.records]
        self.assertEqual(1, messages.count("[test] Started."))
        self.assertIn("Received KeyboardInterrupt, stopping …", messages)
        self.assertIn("Done.", messages)
        self.assertNotIn("Sleeping for 0:00:00.001000 …", messages)

    def test_neutral_result(self) -> None:
        n = 0

        def return_neutral() -> Literal["neutral"]:
            nonlocal n
            n += 1
            return "neutral"

        with (
            TemporaryDirectory() as tmp_dir_str,
            self.assertLogs(level=DEBUG) as logs,
            requests_mock.Mocker() as m,
        ):
            tmp_dir = Path(tmp_dir_str)
            last_run_dir = tmp_dir / "last-run"

            self._mock_healthchecks(m)

            schedule(
                hc_ping_key="ping-key",
                hc_manage_key="manage-key",
                hc_timeout=timedelta(days=1),
                hc_grace=timedelta(days=2),
                interval=timedelta(milliseconds=1),
                max_runs=3,
                heartbeat_path=None,
                name="test",
                description="Test schedule",
                last_run_dir=last_run_dir,
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=return_neutral,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            )

            state = json.loads((last_run_dir / "test.json").read_bytes())

        self.assertEqual(1, n)
        self.assertEqual(
            [
                {"at": AnyFloat(), "kind": "started"},
                {
                    "at": AnyFloat(),
                    "details": "",
                    "kind": "finished",
                    "outcome": "neutral",
                },
            ],
            state,
        )

        self.assertEqual(
            [
                "POST https://healthchecks.io/api/v3/checks/ 200",
                "[test] Checking if it should run …",
                "[test] Started.",
                f"Entering context for health check with slug=test and uuid={health_check_uuid}",
                f"Sending start ping for health check with slug=test and uuid={health_check_uuid}.",
                f"GET https://hc-ping.com/{health_check_uuid}/start?rid="
                + any_uuid
                + " 200",
                "Result for `test` is `neutral`.",
                f"Context for health check with slug=test and uuid={health_check_uuid} exited without exception.",
                "Suppressing success ping for health check with slug=test and uuid=00000000-0000-0000-0000-000000000001",
                "[test] Returned `neutral`.",
                "Sleeping for 0:00:00.001000 …",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "Sleeping for 0:00:00.001000 …",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "Done.",
            ],
            [r.message for r in logs.records],
        )


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
