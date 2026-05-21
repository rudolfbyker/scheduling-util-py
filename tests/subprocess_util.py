from itertools import zip_longest

from comparable_pattern import ComparablePattern

import re
from subprocess import CompletedProcess
from typing import Tuple, List, Sequence, Any, Dict, Literal
from unittest import TestCase


def get_run_output(completed: CompletedProcess[bytes]) -> Tuple[str, str]:
    return normalize(completed.stdout), normalize(completed.stderr)


ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def normalize(b: bytes) -> str:
    """
    - Decode and replace invalid characters
    - Remove all ANSI escape codes.
      - Color codes added by the logger.
      - Depending on the `pip` version and how we run our tests (from the IDE vs using `python -m unittest`) the output
        of `subprocess.run` may or may not have an ANSI escape code at the end of the string. Remove that, too.
    - Remove leading and trailing whitespace
    - Normalize line endings to `\n` to make tests consistent across platforms.
    """

    decoded: str = b.decode(errors="replace", encoding="utf-8")
    return ansi_escape.sub("", decoded).strip().replace("\r\n", "\n")


def assert_process_result(
    *,
    test: TestCase,
    actual_completed_process: CompletedProcess[bytes],
    expected_code: int | None = None,
    expected_stderr: Sequence[str | ComparablePattern] | None = None,
    expected_stdout: Sequence[str | ComparablePattern] | None = None,
    stderr_order_matters: bool = True,
    stdout_order_matters: bool = True,
    stdout_allow_extra: bool = False,
    stderr_allow_extra: bool = False,
) -> None:
    """
    Perform assertions on the results of a completed process, in a way that makes it easy for the developer to see
    which lines are causing the tests to fail.

    Args:
        test: The unittest TestCase instance.
        actual_completed_process: The actual completed process.
        expected_code: The expected return code. `None` means "don't do the assertion".
        expected_stderr: The expected `stderr` lines. `None` means "don't do the assertion".
        expected_stdout: The expected `stdout` lines. `None` means "don't do the assertion".
        stderr_order_matters: Whether the order of lines in stderr matters.
        stdout_order_matters: Whether the order of lines in stdout matters.
        stdout_allow_extra:
            Whether extra lines in stdout are allowed.
            If `True`, the test will only check that the expected lines are present in stdout.
            If `False`, the test will check that stdout contains exactly the expected lines.
        stderr_allow_extra:
            Whether extra lines in stderr are allowed.
            If `True`, the test will only check that the expected lines are present in stderr.
            If `False`, the test will check that stderr contains exactly the expected lines.
    """
    stdout, stderr = get_run_output(actual_completed_process)

    actual: Dict[str, Any] = {}
    expected: Dict[str, Any] = {}
    messages: List[str] = []

    if expected_code is not None:
        expected["code"] = expected_code
        actual["code"] = actual_completed_process.returncode

        if expected["code"] != actual["code"]:
            messages.append("The return code differs.")

    def compare_stream(
        *,
        name: Literal["stderr", "stdout"],
        expected_lines: Sequence[str | ComparablePattern],
        actual_stream: str,
        order_matters: bool,
        allow_extra: bool,
    ) -> None:
        actual_stream_lines = actual_stream.splitlines()
        if not allow_extra and len(expected_lines) != len(actual_stream_lines):
            messages.append(f"The number of lines in {name} differs.")

        differences: List[int] = []

        if order_matters:
            if allow_extra:
                raise NotImplementedError(
                    "Using `order_matters` with `allow_extra` is not implemented yet."
                )

            for i, (expected_line, actual_line) in enumerate(
                zip_longest(expected_lines, actual_stream_lines, fillvalue=None)
            ):
                if expected_line != actual_line:
                    differences.append(i)

            if len(differences):
                messages.append(
                    f"There are {len(differences)} differences in {name}, at line(s) {','.join(map(str,differences))}."
                )

            expected[f"{name} lines"] = expected_lines
            actual[f"{name} lines"] = actual_stream_lines

        else:
            issues = []

            if not allow_extra and len(expected_lines) != len(actual_stream_lines):
                issues.append(
                    f"Expected {len(expected_lines)} lines but got {len(actual_stream_lines)}."
                )

            for i, expected_line in enumerate(expected_lines):
                if expected_line not in actual_stream_lines:
                    issues.append(f"Missing line {i}: {expected_line}")

            if not allow_extra:
                for i, actual_line in enumerate(actual_stream_lines):
                    if actual_line not in expected_lines:
                        issues.append(f"Unexpected line {i}: {actual_line}")

            expected[f"{name} issues"] = []
            actual[f"{name} issues"] = issues
            if len(issues):
                # Include the actual lines for easier debugging.
                actual[f"{name} lines"] = actual_stream_lines

    if expected_stderr is not None:
        compare_stream(
            name="stderr",
            expected_lines=expected_stderr,
            actual_stream=stderr,
            order_matters=stderr_order_matters,
            allow_extra=stderr_allow_extra,
        )

    if expected_stdout is not None:
        compare_stream(
            name="stdout",
            expected_lines=expected_stdout,
            actual_stream=stdout,
            order_matters=stdout_order_matters,
            allow_extra=stdout_allow_extra,
        )

    test.maxDiff = None
    test.assertEqual(
        expected,
        actual,
        msg="\n".join(messages),
    )
