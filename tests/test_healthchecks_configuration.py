from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import requests
import requests_mock
from click.testing import CliRunner

from scheduling_util import RateLimiter, schedule
from scheduling_util._schedule_cli import schedule_cli


class TestHealthchecksConfiguration(TestCase):
    def run_schedule(self, **kwargs: object) -> None:
        """
        Run one scheduler iteration with isolated state.

        Args:
            **kwargs: Healthchecks options and scheduled function overrides.
        """
        with TemporaryDirectory() as directory:
            options = dict(
                hc_uuid="check-id",
                hc_manage_key="manage-key",
                interval=timedelta(0),
                max_runs=1,
                last_run_dir=Path(directory),
                success_period=timedelta(0),
                neutral_period=timedelta(0),
                failure_period=timedelta(0),
                max_failures=3,
                on_max_failures="stall",
                func=Mock(return_value="success"),
                slack_rate_limiter=RateLimiter(minimum_period=timedelta(0)),
            )
            options.update(kwargs)
            schedule(**options)  # type: ignore[arg-type]

    def test_exact_assignment_before_first_run(self) -> None:
        """
        Replace routing before any ping or scheduled work, preserving custom URLs.
        """
        with requests_mock.Mocker() as mock:
            update = mock.post("https://manage.example/api/checks/check-id", json={})
            ping = mock.get(requests_mock.ANY, text="OK")
            work = Mock(side_effect=lambda: self.assertTrue(update.called))
            self.run_schedule(
                hc_channels=("Slack #backups", "Slack #infrastructure"),
                hc_tags="autoactuary backups",
                hc_manage_base_url="https://manage.example/api/",
                hc_ping_base_url="https://ping.example/",
                func=work,
            )
            self.assertEqual(
                update.last_request.json(),
                {
                    "channels": "Slack #backups,Slack #infrastructure",
                    "tags": "autoactuary backups",
                },
            )
            self.assertEqual(update.last_request.headers["X-Api-Key"], "manage-key")
            self.assertEqual(mock.request_history[0].method, "POST")
            self.assertTrue(
                all(
                    r.url.startswith("https://ping.example/check-id")
                    for r in ping.request_history
                )
            )
            work.assert_called_once()

    def test_slug_check_created_before_channel_assignment(self) -> None:
        """
        Configure a slug-based check before its first ping, using the custom management URL.
        """
        with requests_mock.Mocker() as mock:
            create = mock.post(
                "https://manage.example/api/checks/", json={"uuid": "created-check"}
            )
            update = mock.post(
                "https://manage.example/api/checks/created-check", json={}
            )
            mock.get(requests_mock.ANY, text="OK")
            self.run_schedule(
                hc_uuid=None,
                hc_ping_key="ping-key",
                name="backup-scheduler",
                hc_channels=("Slack #backups",),
                hc_manage_base_url="https://manage.example/api/",
            )
            self.assertEqual(create.last_request.json()["slug"], "backup-scheduler")
            self.assertEqual(update.last_request.json(), {"channels": "Slack #backups"})
            self.assertEqual(
                [r.method for r in mock.request_history], ["POST", "POST", "GET", "GET"]
            )

    def test_failed_slug_creation_prevents_start(self) -> None:
        """
        Propagate management failures while resolving or creating a slug-based check.
        """
        with (
            requests_mock.Mocker() as mock,
            patch("scheduling_util._schedule.ScheduleController") as controller,
        ):
            mock.post(requests_mock.ANY, status_code=403)
            with self.assertRaises(requests.HTTPError):
                self.run_schedule(
                    hc_uuid=None,
                    hc_ping_key="ping-key",
                    name="backup-scheduler",
                    hc_channels=("Slack #backups",),
                )
            controller.assert_not_called()

    def test_no_options_leave_assignments_unchanged(self) -> None:
        """
        Omitted routing options never issue a management request.
        """
        with requests_mock.Mocker() as mock:
            mock.get(requests_mock.ANY, text="OK")
            self.run_schedule()
            self.assertTrue(all(r.method == "GET" for r in mock.request_history))
            self.assertEqual(len(mock.request_history), 2)

    def test_tags_only_omit_channels(self) -> None:
        """
        Tags remain a space-separated string and do not change routing.
        """
        with requests_mock.Mocker() as mock:
            update = mock.post(
                "https://healthchecks.io/api/v3/checks/check-id", json={}
            )
            mock.get(requests_mock.ANY, text="OK")
            self.run_schedule(hc_tags="backups production")
            self.assertEqual(update.last_request.json(), {"tags": "backups production"})

    def test_configuration_failures_prevent_start(self) -> None:
        """
        Reject unknown or ambiguous channels, HTTP errors, and request failures before running.
        """
        for response in (
            {"status_code": 400, "json": {"error": "Unknown channel: Slack #missing"}},
            {
                "status_code": 400,
                "json": {"error": "Channel name is not unique: Slack #backups"},
            },
            {"status_code": 403},
            {"status_code": 500},
            {"exc": requests.ConnectionError("unreachable")},
        ):
            with self.subTest(response=response), requests_mock.Mocker() as mock:
                mock.post(requests_mock.ANY, **response)
                work = Mock()
                with patch(
                    "scheduling_util._schedule.ScheduleController"
                ) as controller:
                    with self.assertRaises(requests.RequestException):
                        self.run_schedule(hc_channels=("Slack #backups",), func=work)
                work.assert_not_called()
                controller.assert_not_called()

    def test_missing_manage_key(self) -> None:
        """
        Reject routing configuration without a management key through the Python API.
        """
        with self.assertRaisesRegex(ValueError, "hc_manage_key is required"):
            self.run_schedule(hc_channels=("Slack #backups",), hc_manage_key=None)

    def test_missing_check_identity(self) -> None:
        """
        Reject channel configuration that cannot identify a check.
        """
        with self.assertRaisesRegex(ValueError, "require hc_uuid"):
            self.run_schedule(hc_channels=("Slack #backups",), hc_uuid=None)

    def test_cli_options_and_environment(self) -> None:
        """
        Accept repeated CLI channels and one complete environment channel name.
        """
        for args, env, channels in (
            (
                [
                    "--hc-channel",
                    "Slack #backups",
                    "--hc-channel",
                    "Slack #infrastructure",
                ],
                {},
                ("Slack #backups", "Slack #infrastructure"),
            ),
            ([], {"AA_SCHEDULER_HC_CHANNEL": "Slack #backups"}, ("Slack #backups",)),
            (
                [],
                {"AA_SCHEDULER_HC_CHANNEL": "Slack #infrastructure"},
                ("Slack #infrastructure",),
            ),
            ([], {}, None),
        ):
            with (
                self.subTest(channels=channels),
                patch("scheduling_util._schedule_cli.schedule") as run,
            ):
                result = CliRunner().invoke(
                    schedule_cli,
                    [
                        "--hc-uuid",
                        "check-id",
                        "--hc-manage-key",
                        "manage-key",
                        *args,
                        "py-exec",
                        "--code",
                        "pass",
                    ],
                    env={
                        **env,
                        "AA_SCHEDULER_HC_TAGS": "backups production",
                        "AA_SCHEDULER_HC_PING_BASE_URL": "https://ping.example/",
                        "AA_SCHEDULER_HC_MANAGE_BASE_URL": "https://manage.example/api/",
                    },
                    auto_envvar_prefix="AA_SCHEDULER",
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(run.call_args.kwargs["hc_channels"], channels)
                self.assertEqual(run.call_args.kwargs["hc_tags"], "backups production")
                self.assertEqual(
                    run.call_args.kwargs["hc_ping_base_url"], "https://ping.example/"
                )
                self.assertEqual(
                    run.call_args.kwargs["hc_manage_base_url"],
                    "https://manage.example/api/",
                )

    def test_cli_missing_manage_key(self) -> None:
        """
        Report a clear usage error for channels or tags without a management key.
        """
        for option in ("--hc-channel", "--hc-tags"):
            with (
                self.subTest(option=option),
                patch("scheduling_util._schedule_cli.schedule") as run,
            ):
                result = CliRunner().invoke(
                    schedule_cli, [option, "backups", "py-exec", "--code", "pass"]
                )
                self.assertEqual(result.exit_code, 2)
                self.assertIn("--hc-manage-key is required", result.output)
                run.assert_not_called()

    def test_cli_defaults_and_explicit_tags(self) -> None:
        """
        Omitted routing remains untouched; explicit tags preserve their string value.
        """
        for args, expected_tags in (
            ([], None),
            (["--hc-tags", "production backups"], "production backups"),
        ):
            with (
                self.subTest(args=args),
                patch("scheduling_util._schedule_cli.schedule") as run,
            ):
                result = CliRunner().invoke(
                    schedule_cli,
                    [
                        "--hc-uuid",
                        "check-id",
                        "--hc-manage-key",
                        "manage-key",
                        *args,
                        "py-exec",
                        "--code",
                        "pass",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIsNone(run.call_args.kwargs["hc_channels"])
                self.assertEqual(run.call_args.kwargs["hc_tags"], expected_tags)

    def test_cli_missing_identity(self) -> None:
        """
        Report a usage error if routing configuration cannot identify a check.
        """
        with patch("scheduling_util._schedule_cli.schedule") as run:
            result = CliRunner().invoke(
                schedule_cli,
                [
                    "--hc-manage-key",
                    "manage-key",
                    "--hc-channel",
                    "Slack #backups",
                    "py-exec",
                    "--code",
                    "pass",
                ],
            )
            self.assertEqual(result.exit_code, 2)
            self.assertIn("require --hc-uuid", result.output)
            run.assert_not_called()

    def test_neutral_outcome_preserves_ping_behavior(self) -> None:
        """
        Channel configuration leaves the current scheduler's neutral outcome behavior intact.
        """
        with requests_mock.Mocker() as mock:
            mock.post("https://healthchecks.io/api/v3/checks/check-id", json={})
            ping = mock.get(requests_mock.ANY, text="OK")
            self.run_schedule(hc_channels=("Slack #backups",), func=lambda: "neutral")
            self.assertEqual(len(ping.request_history), 1)
            self.assertIn("/start?", ping.last_request.url)
