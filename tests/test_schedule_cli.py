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
            expected_stdout=["Usage: schedule.py [OPTIONS] COMMAND [ARGS]..."],
            stdout_allow_extra=True,
            stdout_order_matters=False,
        )

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
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli INFO: hello from py-exec",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
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
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._example_commands INFO: Hello Bertus!",
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
                    "echo",
                    "Hello world",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli.subprocess INFO: Hello world",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__shell(self) -> None:
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
                    "--shell",
                    """\
echo "Hello"
echo "World"
""",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._schedule_cli.subprocess INFO: Hello",
                    "scheduling_util._schedule_cli.subprocess INFO: World",
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
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._send_errors_to_slack ERROR: "
                    "FileNotFoundError: [Errno 2] No such file or directory: 'this-program-does-not-exist'",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__check(self) -> None:
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
                    "false",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._send_errors_to_slack ERROR: "
                    "CalledProcessError: Command '('false',)' returned non-zero exit status 1.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )

    def test_subprocess__no_check(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

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
                    "false",
                ],
                capture_output=True,
                env={**os.environ, "PYTHONPATH": repo_dir.as_posix()},
            )

            assert_process_result(
                test=self,
                actual_completed_process=completed,
                expected_code=0,
                expected_stderr=[
                    "scheduling_util._last_run DEBUG: [test] Checking if it should run …",
                    "scheduling_util._last_run DEBUG: [test] Started.",
                    'scheduling_util._schedule_cli.subprocess DEBUG: Starting process: ["false"]',
                    "scheduling_util._last_run DEBUG: [test] Succeeded.",
                    "scheduling_util._schedule INFO: Done.",
                ],
                expected_stdout=[],
            )
