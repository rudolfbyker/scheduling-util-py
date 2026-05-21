import os
import sys
import unittest
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from .subprocess_util import assert_process_result

repo_dir = Path(__file__).resolve().parent.parent
schedule_script_path = repo_dir / "scripts/schedule.py"


class TestScheduleCli(unittest.TestCase):

    def test_help(self) -> None:
        completed = run(
            args=[
                sys.executable,
                schedule_script_path,
                "--help",
            ],
            capture_output=True,
            env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
        )

        assert_process_result(
            test=self,
            actual_completed_process=completed,
            expected_code=0,
            expected_stderr=[],
            expected_stdout=["Usage: schedule.py [OPTIONS]"],
            stdout_allow_extra=True,
            stdout_order_matters=False,
        )

    def test_no_execution_mode(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level",
                    "info",
                    "--hc-ping-key",
                    "ping-key",
                    "--hc-manage-key",
                    "manage-key",
                    "--hc-timeout",
                    "1d",
                    "--hc-grace",
                    "2d",
                    "--interval",
                    "1s",
                    "--slug",
                    "test",
                    "--cache-dir",
                    str(tmp_dir),
                    "--success-period",
                    "1d",
                    "--failure-period",
                    "1h",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=2,
                expected_stderr=[
                    "Error: Either --command or --py-exec must be provided."
                ],
                stderr_allow_extra=True,
                stderr_order_matters=False,
                expected_stdout=[],
            )

    def test_both_execution_modes(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            completed = run(
                args=[
                    sys.executable,
                    schedule_script_path,
                    "--log-level",
                    "info",
                    "--hc-ping-key",
                    "ping-key",
                    "--hc-manage-key",
                    "manage-key",
                    "--hc-timeout",
                    "1d",
                    "--hc-grace",
                    "2d",
                    "--interval",
                    "1s",
                    "--slug",
                    "test",
                    "--cache-dir",
                    str(tmp_dir),
                    "--success-period",
                    "1d",
                    "--failure-period",
                    "1h",
                    "--command",
                    "echo hello",
                    "--py-exec",
                    "print('hello')",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=2,
                expected_stderr=[
                    "Error: Only one of --command or --py-exec may be provided."
                ],
                stderr_allow_extra=True,
                stderr_order_matters=False,
                expected_stdout=[],
            )
