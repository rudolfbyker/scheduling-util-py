import os
import re
import shlex
import sys
import unittest
from pathlib import Path
from subprocess import list2cmdline, run
from tempfile import TemporaryDirectory
from unittest.mock import patch

from comparable_pattern import ComparablePattern
from click.testing import CliRunner

from .subprocess_util import assert_process_result

from scheduling_util._schedule_cli import schedule_cli

repo_dir = Path(__file__).resolve().parent.parent
schedule_script_path = repo_dir / "scripts/schedule.py"
hc_uuid = "my-health-check"


def schedule_script_env() -> dict[str, str]:
    python_path = os.pathsep.join(
        [
            (repo_dir / "src").as_posix(),
            repo_dir.as_posix(),
        ]
    )
    return {
        **os.environ,
        "PYTHONPATH": python_path,
        "PYTHONIOENCODING": "utf-8",
    }


def shell_command(args: list[str]) -> str:
    if os.name == "nt":
        return list2cmdline(args)

    return shlex.join(args)


class TestScheduleCli(unittest.TestCase):

    def test_help(self) -> None:
        completed = run(
            args=[
                sys.executable,
                schedule_script_path,
                "--help",
            ],
            capture_output=True,
            env=schedule_script_env(),
        )

        assert_process_result(
            test=self,
            actual_completed_process=completed,
            expected_code=0,
            expected_stderr=[],
            expected_stdout=["Usage: schedule.py [OPTIONS] COMMAND [ARGS]..."],
            stdout_allow_extra=True,
            stdout_order_matters=False,
        )

    def test_help__healthchecks_options(self) -> None:
        result = CliRunner().invoke(schedule_cli, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--hc-ping-key", result.output)
        self.assertIn("--hc-manage-key", result.output)
        self.assertIn("--hc-timeout", result.output)
        self.assertIn("--hc-grace", result.output)
        self.assertIn("--hc-uuid", result.output)
        self.assertIn("The UUID of the health check.", result.output)
        self.assertIn("healthchecks.io", result.output)

    def test_hc_uuid__forwarded_to_schedule(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            with patch("scheduling_util._schedule_cli.schedule") as schedule_mock:
                result = CliRunner().invoke(
                    schedule_cli,
                    [
                        "--hc-uuid",
                        hc_uuid,
                        "--hc-ping-key",
                        "ping-key",
                        "--hc-manage-key",
                        "manage-key",
                        "--hc-timeout",
                        "5m",
                        "--hc-grace",
                        "10m",
                        "--max-runs=1",
                        "--cache-dir",
                        tmp_dir_str,
                        "py-exec",
                        "--code",
                        "pass",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        schedule_mock.assert_called_once()
        schedule_kwargs = schedule_mock.call_args.kwargs
        self.assertEqual(schedule_kwargs["hc_uuid"], hc_uuid)
        self.assertEqual(schedule_kwargs["hc_ping_key"], "ping-key")
        self.assertEqual(schedule_kwargs["hc_manage_key"], "manage-key")
        self.assertEqual(schedule_kwargs["hc_timeout"].total_seconds(), 300)
        self.assertEqual(schedule_kwargs["hc_grace"].total_seconds(), 600)

    def test_ipc_socket__forwarded_to_schedule(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"

            with patch("scheduling_util._schedule_cli.schedule") as schedule_mock:
                result = CliRunner().invoke(
                    schedule_cli,
                    [
                        "--ipc-socket",
                        str(socket_path),
                        "--max-runs=1",
                        "--cache-dir",
                        tmp_dir_str,
                        "py-exec",
                        "--code",
                        "pass",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        schedule_mock.assert_called_once()
        schedule_kwargs = schedule_mock.call_args.kwargs
        self.assertEqual(socket_path.resolve(), schedule_kwargs["ipc_socket_path"])

    def test_py_exec(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "py-exec",
                    "--code",
                    """\
from logging import getLogger
logger = getLogger(__name__)
logger.info("hello from py-exec")
""",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli INFO: hello from py-exec",
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_py_exec__exception(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "py-exec",
                    "--code",
                    """\
raise RuntimeError("error from py-exec")
""",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._send_errors_to_slack ERROR: RuntimeError: error from py-exec",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_py_exec__exit_code_failure(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "py-exec",
                    "--code",
                    """\
import sys
sys.exit(16)
""",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `failure`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_py_exec__exit_code_neutral(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "py-exec",
                    "--exit-codes-neutral=16",
                    "--code",
                    """\
import sys
sys.exit(16)
""",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `neutral`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_py_exec__exit_code_success(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "py-exec",
                    "--exit-codes-success=16",
                    "--code",
                    """\
import sys
sys.exit(16)
""",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_exit_code_success_and_neutral_must_not_overlap(self) -> None:
        cases = [
            [
                "py-exec",
                "--exit-codes-success=16",
                "--exit-codes-neutral=16",
                "--code=pass",
            ],
            [
                "click-invoke",
                "--exit-codes-success=16",
                "--exit-codes-neutral=16",
                "--command=scheduling_util:hello",
            ],
            [
                "subprocess",
                "--exit-codes-success=16",
                "--exit-codes-neutral=16",
                sys.executable,
                "-c",
                "pass",
            ],
        ]

        for args in cases:
            with self.subTest(command=args[0]):
                with TemporaryDirectory() as tmp_dir_str:
                    result = CliRunner().invoke(
                        schedule_cli,
                        [
                            "--max-runs=1",
                            "--cache-dir",
                            tmp_dir_str,
                            *args,
                        ],
                    )

                self.assertEqual(2, result.exit_code, result.output)
                self.assertIn(
                    "Exit codes cannot be both successful and neutral: 16.",
                    result.output,
                )

    def test_click_invoke(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "click-invoke",
                    "--command=scheduling_util:hello",
                    "--name",
                    "Bertus",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._example_commands INFO: Hello Bertus!",
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_click_invoke__exception(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "click-invoke",
                    "--command=scheduling_util:raise_exception",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._send_errors_to_slack ERROR: ClickException: meh",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_click_invoke__exit_code_failure(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "click-invoke",
                    "--command=scheduling_util:exit_code",
                    "--code=16",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `failure`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_click_invoke__exit_code_neutral(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "click-invoke",
                    "--exit-codes-neutral=16",
                    "--command=scheduling_util:exit_code",
                    "--code=16",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `neutral`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_click_invoke__exit_code_success(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "click-invoke",
                    "--exit-codes-success=16",
                    "--command=scheduling_util:exit_code",
                    "--code=16",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    sys.executable,
                    "-c",
                    "print('Hello world')",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli.subprocess INFO: Hello world",
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__shell(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            command = shell_command(
                [sys.executable, "-c", "print('Hello'); print('World')"]
            )

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "--shell",
                    command,
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli.subprocess INFO: Hello",
                    "scheduling_util._schedule_cli.subprocess INFO: World",
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__command_not_found(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "this-program-does-not-exist",
                    "some",
                    "args",
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    ComparablePattern(
                        re.compile(
                            r"scheduling_util\._send_errors_to_slack ERROR: "
                            r"FileNotFoundError: .*"
                        )
                    ),
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__check(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            command = [sys.executable, "-c", "raise SystemExit(1)"]

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "--check",
                    *command,
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._send_errors_to_slack ERROR: "
                    f"CalledProcessError: Command '{tuple(command)}' returned non-zero exit status 1.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__no_check(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            command = [sys.executable, "-c", "raise SystemExit(1)"]

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=debug",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "--no-check",
                    *command,
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._last_run DEBUG: [test] Checking if it should run …",
                    "scheduling_util._last_run DEBUG: [test] Started.",
                    ComparablePattern(
                        re.compile(
                            r"scheduling_util\._schedule_cli\.subprocess DEBUG: "
                            r'Starting process: \[.*"raise SystemExit\(1\)".*]'
                        )
                    ),
                    "scheduling_util._schedule INFO: Result for `test` is `failure`.",
                    "scheduling_util._last_run DEBUG: [test] Returned `failure`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__exit_code__neutral(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            command = [sys.executable, "-c", "raise SystemExit(16)"]

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "--exit-codes-neutral=16",
                    *command,
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `neutral`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__exit_code__success(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            command = [sys.executable, "-c", "raise SystemExit(16)"]

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level=info",
                    "--max-runs=1",
                    "--name=test",
                    "--cache-dir",
                    str(tmp_dir),
                    "subprocess",
                    "--exit-codes-success=16",
                    *command,
                ],
                capture_output=True,
                env=schedule_script_env(),
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule INFO: Result for `test` is `success`.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )
