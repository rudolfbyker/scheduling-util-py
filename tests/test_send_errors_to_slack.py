import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from scheduling_util import RateLimiter, send_errors_to_slack


class TestSendErrorsToSlack(unittest.TestCase):
    def test_no_exception_does_not_post(self) -> None:
        limiter = MagicMock(spec=RateLimiter)

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ) as post_mock:
            with self.assertNoLogs(
                "scheduling_util._send_errors_to_slack",
                level="DEBUG",
            ):
                with send_errors_to_slack(
                    slack_webhook="https://example.test/hook",
                    reraise=True,
                    limiter=limiter,
                    log_error=True,
                ):
                    pass

        post_mock.assert_not_called()
        limiter.ping_if_ready.assert_not_called()

    def test_exception_with_webhook_and_ready_limiter_posts_to_slack(self) -> None:
        limiter = MagicMock(spec=RateLimiter)
        limiter.ping_if_ready.return_value = True

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ) as post_mock:
            with self.assertLogs(
                "scheduling_util._send_errors_to_slack",
                level="ERROR",
            ) as logs:
                with send_errors_to_slack(
                    slack_webhook="https://example.test/hook",
                    reraise=False,
                    limiter=limiter,
                    log_error=False,
                ):
                    raise ValueError("boom")

        limiter.ping_if_ready.assert_called_once_with()
        post_mock.assert_called_once()
        self.assertEqual(
            ["ConnectionError: network down"],
            [r.message for r in logs.records],
        )
        kwargs = post_mock.call_args.kwargs
        self.assertEqual("https://example.test/hook", kwargs["url"])
        self.assertEqual(30, kwargs["timeout"])

        payload = json.loads(kwargs["data"])
        self.assertEqual(
            "`ValueError`:\n```\nboom\n```", payload["blocks"][0]["text"]["text"]
        )
        self.assertEqual("traceback_block", payload["blocks"][1]["block_id"])
        self.assertIn("_send_errors_to_slack.py", payload["blocks"][1]["text"]["text"])

    def test_exception_with_reraise_true_reraises_original_error(self) -> None:
        limiter = MagicMock(spec=RateLimiter)
        limiter.ping_if_ready.return_value = True

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ):
            with self.assertLogs(
                "scheduling_util._send_errors_to_slack",
                level="ERROR",
            ) as logs:
                with self.assertRaisesRegex(RuntimeError, "explode"):
                    with send_errors_to_slack(
                        slack_webhook="https://example.test/hook",
                        reraise=True,
                        limiter=limiter,
                        log_error=False,
                    ):
                        raise RuntimeError("explode")

        self.assertEqual(
            ["ConnectionError: network down"],
            [r.message for r in logs.records],
        )

    def test_exception_without_webhook_skips_post_and_limiter(self) -> None:
        limiter = MagicMock(spec=RateLimiter)

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ) as post_mock:
            with send_errors_to_slack(
                slack_webhook=None,
                reraise=False,
                limiter=limiter,
                log_error=False,
            ):
                raise RuntimeError("ignored")

        post_mock.assert_not_called()
        limiter.ping_if_ready.assert_not_called()

    def test_exception_when_limiter_not_ready_logs_warning_and_skips_post(self) -> None:
        limiter = MagicMock(spec=RateLimiter)
        limiter.ping_if_ready.return_value = False
        limiter.timestamp = datetime(2026, 1, 2, 3, 4, 5)

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ) as post_mock:
            with self.assertLogs(level="WARNING") as logs:
                with send_errors_to_slack(
                    slack_webhook="https://example.test/hook",
                    reraise=False,
                    limiter=limiter,
                    log_error=False,
                ):
                    raise RuntimeError("rate-limited")

        post_mock.assert_not_called()
        limiter.ping_if_ready.assert_called_once_with()
        self.assertEqual(1, len(logs.records))
        self.assertIn(
            "Not posting to Slack to avoid hitting rate limits", logs.records[0].message
        )
        self.assertIn(limiter.timestamp.isoformat(), logs.records[0].message)

    def test_exception_when_post_fails_logs_error(self) -> None:
        limiter = MagicMock(spec=RateLimiter)
        limiter.ping_if_ready.return_value = True

        with patch(
            target="scheduling_util._send_errors_to_slack.post",
            side_effect=ConnectionError("network down"),
        ):
            with self.assertLogs(level="ERROR") as logs:
                with send_errors_to_slack(
                    slack_webhook="https://example.test/hook",
                    reraise=False,
                    limiter=limiter,
                    log_error=False,
                ):
                    raise RuntimeError("primary error")

        self.assertEqual(1, len(logs.records))
        self.assertIn("network down", logs.records[0].message)
