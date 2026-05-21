import unittest
from datetime import timedelta, datetime
from inspect import signature
from logging import DEBUG, ERROR
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Optional, Union

from scheduling_util import LastRun, create_run_predicate, LastRunState
from .util import AnyDateTime, MatchingException


def always(
    now: datetime,
    state: LastRunState,
) -> bool:
    return True


class TestLastRun(unittest.TestCase):
    def test_wrap__preserve_signature_and_docstring(self) -> None:
        """
        Check the function name, signature, and docstring.
        """
        with TemporaryDirectory() as tmp_dir_str:
            last = LastRun(
                path=Path(tmp_dir_str, "last_run.txt"),
                name="test",
            )

            def run(foo: str, bar: int, *, baz: bool) -> int:
                """
                Sample function.
                """
                return 1

            run_debounced = last.wrap(f=run, should_run=always)

            self.assertEqual("run", run_debounced.__name__)
            self.assertEqual(
                # The same as the original function signature, except that it may also return None (when skipped).
                "(foo: str, bar: int, *, baz: bool) -> Optional[int]",
                str(signature(run_debounced)),
            )
            self.assertEqual("Sample function.", (run_debounced.__doc__ or "").strip())

    def test_wrap__return_types_none(self) -> None:
        """
        These return types already include `None`, so they should not be modified by the wrapper.
        """
        with TemporaryDirectory() as tmp_dir_str:
            last = LastRun(
                path=Path(tmp_dir_str, "last_run.txt"),
                name="test",
            )

            def f1() -> None:
                return None

            def f2() -> int | None:
                return 1

            def f3() -> None | int:
                return 2

            def f4() -> Optional[bool]:
                return True

            def f5() -> Union[int, str, None]:
                return "a"

            def f6():  # type: ignore[no-untyped-def]
                pass

            for f in [f1, f2, f3, f4, f5, f6]:
                wrapped = last.wrap(f=f, should_run=always)
                self.assertEqual(str(signature(f)), str(signature(wrapped)))

    def test_wrap__debounce_success(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            last = LastRun(
                path=tmp_dir / "last_run.txt",
                name="test",
            )

            n = 0

            def run() -> None:
                nonlocal n
                n += 1

            run_debounced = last.wrap(
                f=run,
                should_run=create_run_predicate(
                    success_period=timedelta(seconds=0.1),
                    failure_period=timedelta(seconds=10),
                    max_failures=0,
                    on_max_failures="ignore",
                ),
            )

            with self.assertLogs(level=DEBUG) as logs:
                run_debounced()
                self.assertEqual(n, 1)

                run_debounced()
                self.assertEqual(n, 1)

                run_debounced()
                self.assertEqual(n, 1)

                sleep(0.15)

                run_debounced()
                self.assertEqual(n, 2)

                run_debounced()
                self.assertEqual(n, 2)

                run_debounced()
                self.assertEqual(n, 2)

        self.assertEqual(
            [
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Succeeded.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Succeeded.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
            ],
            [r.message for r in logs.records],
        )

    def test_wrap__errors(self) -> None:
        """
        Check error handling and logging.
        """
        with TemporaryDirectory() as tmp_dir_str:
            last = LastRun(
                path=Path(tmp_dir_str, "last_run.txt"),
                name="test",
            )

            def run(message: str) -> None:
                raise RuntimeError(message)

            run_debounced = last.wrap(f=run, should_run=always)

            with self.assertLogs(level=DEBUG) as logs:
                for message in ["a", "b", "c", "d", "e"]:
                    with self.assertRaises(RuntimeError):
                        run_debounced(message)

        self.assertEqual(
            [
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Failed: a",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Failed: b",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Failed: c",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Failed: d",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Failed: e",
            ],
            [r.message for r in logs.records],
        )

        self.assertEqual(
            LastRunState(
                last_attempted=AnyDateTime(),
                last_failed=AnyDateTime(),
                last_failure=MatchingException(
                    match_message="e",
                    match_type=RuntimeError,
                ),
                last_successful=None,
                n_consecutive_failures=5,
            ),
            last.state,
        )


class TestCreateRunPredicate(unittest.TestCase):
    def test_on_max_failures__success_schedule(self) -> None:
        p = create_run_predicate(
            success_period=timedelta(days=1),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        with self.subTest("Initial state: Always run."):
            self.assertTrue(
                p(
                    now=t,
                    state=LastRunState(
                        last_attempted=None,
                        last_successful=None,
                        last_failed=None,
                        n_consecutive_failures=0,
                        last_failure=None,
                    ),
                )
            )

        with self.subTest("After a successful run: Wait for a day."):
            success_5_min_ago = LastRunState(
                last_attempted=t - timedelta(minutes=6),
                last_successful=t - timedelta(minutes=5),
                last_failed=None,
                n_consecutive_failures=0,
                last_failure=None,
            )
            self.assertFalse(p(now=t, state=success_5_min_ago))
            self.assertFalse(p(now=t + timedelta(hours=23), state=success_5_min_ago))
            self.assertTrue(p(now=t + timedelta(hours=25), state=success_5_min_ago))

        with self.subTest("After the first failed run: Wait for an hour."):
            failure_5_min_ago = LastRunState(
                last_attempted=t - timedelta(minutes=6),
                last_successful=None,
                last_failed=t - timedelta(minutes=5),
                n_consecutive_failures=1,
                last_failure=None,
            )
            self.assertFalse(p(now=t, state=failure_5_min_ago))
            self.assertFalse(p(now=t + timedelta(minutes=55), state=failure_5_min_ago))
            self.assertTrue(p(now=t + timedelta(minutes=65), state=failure_5_min_ago))

        with self.subTest(
            "After too many failed runs: Wait for a day after the last successful run."
        ):
            too_many_failures = LastRunState(
                last_attempted=t - timedelta(minutes=6),
                last_successful=t - timedelta(hours=1),
                last_failed=t - timedelta(minutes=5),
                n_consecutive_failures=3,
                last_failure="Meh",
            )
            self.assertFalse(p(now=t, state=too_many_failures))
            self.assertFalse(p(now=t + timedelta(hours=22), state=too_many_failures))
            self.assertTrue(p(now=t + timedelta(hours=24), state=too_many_failures))

        with self.subTest(
            "Too many failed runs, and no previous successful run: Log an error, and don't run."
        ):
            too_many_failures_no_success = LastRunState(
                last_attempted=t - timedelta(minutes=6),
                last_successful=None,
                last_failed=t - timedelta(minutes=5),
                n_consecutive_failures=3,
                last_failure="Meh",
            )
            with self.assertLogs(level=ERROR):
                self.assertFalse(p(now=t, state=too_many_failures_no_success))

        with self.subTest("Missing 'last failed' time"):
            missing_last_failed = LastRunState(
                last_attempted=t - timedelta(minutes=6),
                last_successful=None,
                last_failed=None,
                n_consecutive_failures=1,
                last_failure=None,
            )
            with self.assertLogs(level=ERROR):
                self.assertFalse(p(now=t, state=missing_last_failed))

    def test_on_max_failures__stall(self) -> None:
        p = create_run_predicate(
            success_period=timedelta(days=1),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="stall",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunState(
            last_attempted=t - timedelta(minutes=6),
            last_successful=t - timedelta(hours=1),
            last_failed=t - timedelta(minutes=5),
            n_consecutive_failures=3,
            last_failure="Meh",
        )
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t, state=too_many_failures))
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t + timedelta(days=100), state=too_many_failures))

    def test_on_max_failures__ignore(self) -> None:
        p = create_run_predicate(
            success_period=timedelta(days=1),
            failure_period=timedelta(hours=1),
            max_failures=1,
            on_max_failures="ignore",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunState(
            last_attempted=t - timedelta(minutes=6),
            last_successful=t - timedelta(hours=1),
            last_failed=t - timedelta(minutes=5),
            n_consecutive_failures=10,
            last_failure="Meh",
        )
        self.assertFalse(p(now=t, state=too_many_failures))
        self.assertTrue(p(now=t + timedelta(hours=2), state=too_many_failures))

    def test_on_max_failures__invalid(self) -> None:
        p = create_run_predicate(
            success_period=timedelta(days=1),
            failure_period=timedelta(hours=1),
            max_failures=1,
            on_max_failures="foo",  # type: ignore[arg-type]
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunState(
            last_attempted=t - timedelta(minutes=6),
            last_successful=t - timedelta(hours=1),
            last_failed=t - timedelta(minutes=5),
            n_consecutive_failures=10,
            last_failure="Meh",
        )
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t, state=too_many_failures))
