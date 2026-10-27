"""
Check endpoint routing through the scheduler CLI and real Healthchecks client.
"""

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests_mock
from click.testing import CliRunner

from scheduling_util import RateLimiter, schedule
from scheduling_util._schedule_cli import schedule_cli


class TestHealthchecksEndpoints(unittest.TestCase):
    """
    Verify custom endpoints without contacting either Healthchecks service.
    """

    def test_cli_environment_and_defaults(self) -> None:
        """
        Forward wrapper environment settings while preserving hosted defaults.
        """
        for env, expected_ping, expected_manage in [
            ({}, "https://hc-ping.com/", "https://healthchecks.io/api/v3/"),
            (
                {
                    "AA_HC_PING_BASE_URL": "https://hc.example.org/ping/",
                    "AA_HC_MANAGE_BASE_URL": "https://hc.example.org/api/v3/",
                },
                "https://hc.example.org/ping/",
                "https://hc.example.org/api/v3/",
            ),
        ]:
            with (
                self.subTest(env=env),
                patch("scheduling_util._schedule_cli.schedule") as mocked,
            ):
                result = CliRunner().invoke(
                    schedule_cli,
                    ["--max-runs=1", "py-exec", "--code", "pass"],
                    env=env,
                    auto_envvar_prefix="AA",
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(
                    mocked.call_args.kwargs["hc_ping_base_url"], expected_ping
                )
                self.assertEqual(
                    mocked.call_args.kwargs["hc_manage_base_url"], expected_manage
                )

    def test_custom_requests_and_preserved_schedule_state(self) -> None:
        """
        Route API and ping traffic locally without rerunning completed work.
        """
        with TemporaryDirectory() as temporary, requests_mock.Mocker() as mocked:
            root = Path(temporary)
            mocked.post(
                "https://hc.example.org/api/v3/checks/",
                json={"uuid": "00000000-0000-0000-0000-000000000001"},
            )
            mocked.register_uri(
                requests_mock.ANY,
                requests_mock.ANY,
                text="OK",
            )
            # Register the specific API response last so it wins over the catch-all.
            mocked.post(
                "https://hc.example.org/api/v3/checks/",
                json={"uuid": "00000000-0000-0000-0000-000000000001"},
            )
            completed = []

            def work() -> None:
                """
                Record an actual job execution.
                """
                completed.append(True)

            for _ in range(2):
                schedule(
                    hc_ping_base_url="https://hc.example.org/ping/",
                    hc_manage_base_url="https://hc.example.org/api/v3/",
                    hc_ping_key="ping-key",
                    hc_manage_key="manage-key",
                    interval=timedelta(0),
                    max_runs=1,
                    name="backup",
                    last_run_dir=root / "last_run",
                    success_period=timedelta(days=30),
                    neutral_period=timedelta(hours=1),
                    failure_period=timedelta(days=1),
                    max_failures=3,
                    on_max_failures="stall",
                    func=work,
                    slack_rate_limiter=RateLimiter(
                        minimum_period=timedelta(0), path=root / "slack"
                    ),
                )

            self.assertEqual(completed, [True])
            self.assertTrue(
                any(
                    "/api/v3/checks/" in request.url
                    for request in mocked.request_history
                )
            )
            self.assertTrue(
                any("/ping/" in request.url for request in mocked.request_history)
            )
            self.assertTrue(
                all(
                    request.hostname == "hc.example.org"
                    for request in mocked.request_history
                )
            )
