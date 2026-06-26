import unittest
from datetime import timedelta, datetime
from inspect import signature
from logging import DEBUG, ERROR
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Optional, Union

from scheduling_util import LastRun, RunPredicate, LastRunHistory
from scheduling_util._last_run_history import LastRunAttemptFinished


def always(
    now: datetime,
    history: LastRunHistory,
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
                should_run=RunPredicate(
                    success_period=timedelta(seconds=0.1),
                    neutral_period=timedelta(seconds=1),
                    failure_period=timedelta(seconds=10),
                    max_failures=0,
                    on_max_failures="ignore",
                ),
            )

            with self.assertLogs(level=DEBUG) as logs:
                self.assertEqual([], last.history.entries)

                run_debounced()
                self.assertEqual(n, 1)

                self.assertEqual(
                    ["started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

                run_debounced()
                self.assertEqual(n, 1)

                self.assertEqual(
                    ["started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

                run_debounced()
                self.assertEqual(n, 1)

                self.assertEqual(
                    ["started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

                sleep(0.15)

                run_debounced()
                self.assertEqual(n, 2)

                self.assertEqual(
                    ["started", "finished", "started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

                run_debounced()
                self.assertEqual(n, 2)

                self.assertEqual(
                    ["started", "finished", "started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

                run_debounced()
                self.assertEqual(n, 2)

                self.assertEqual(
                    ["started", "finished", "started", "finished"],
                    [entry.kind for entry in last.history.entries],
                )

        self.assertEqual(
            [
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Returned `None`.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "[test] Checking if it should run …",
                "[test] Skipped.",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Returned `None`.",
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

            def run(msg: str) -> None:
                raise RuntimeError(msg)

            run_debounced = last.wrap(f=run, should_run=always)

            with self.assertLogs(level=DEBUG) as logs:
                for message in ["a", "b", "c", "d", "e"]:
                    with self.assertRaises(RuntimeError):
                        run_debounced(message)

        self.assertEqual(
            [
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Raised `RuntimeError`: a",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Raised `RuntimeError`: b",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Raised `RuntimeError`: c",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Raised `RuntimeError`: d",
                "[test] Checking if it should run …",
                "[test] Started.",
                "[test] Raised `RuntimeError`: e",
            ],
            [r.message for r in logs.records],
        )

        self.assertEqual(
            [
                "started",
                "finished failure Raised `RuntimeError`: a",
                "started",
                "finished failure Raised `RuntimeError`: b",
                "started",
                "finished failure Raised `RuntimeError`: c",
                "started",
                "finished failure Raised `RuntimeError`: d",
                "started",
                "finished failure Raised `RuntimeError`: e",
            ],
            [
                f"{entry.kind}"
                + (
                    f" {entry.outcome} {entry.details}"
                    if isinstance(entry, LastRunAttemptFinished)
                    else ""
                )
                for entry in last.history.entries
            ],
        )


class TestRunPredicate(unittest.TestCase):
    def test_force_next_return_value__forces_true_once(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)
        success_5_min_ago = LastRunHistory(
            entries=[
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=5)).timestamp(),
                    outcome="success",
                )
            ]
        )

        self.assertFalse(p(now=t, history=success_5_min_ago))

        p.force_next_return_value(True)
        self.assertTrue(p(now=t, history=success_5_min_ago))
        self.assertFalse(p(now=t, history=success_5_min_ago))

    def test_force_next_return_value__forces_false_once(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)
        empty_history = LastRunHistory()

        self.assertTrue(p(now=t, history=empty_history))

        p.force_next_return_value(False)
        self.assertFalse(p(now=t, history=empty_history))
        self.assertTrue(p(now=t, history=empty_history))

    def test_unset_next_return_value__clears_pending_forced_value(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)
        success_5_min_ago = LastRunHistory(
            entries=[
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=5)).timestamp(),
                    outcome="success",
                )
            ]
        )

        p.force_next_return_value(True)
        p.unset_next_return_value()

        self.assertFalse(p(now=t, history=success_5_min_ago))

    def test_force_next_return_value__overwrites_pending_forced_value(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)
        empty_history = LastRunHistory()

        p.force_next_return_value(False)
        p.force_next_return_value(True)

        self.assertTrue(p(now=t, history=empty_history))

    def test_on_max_failures__success_schedule(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="success_schedule",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        with self.subTest("Initial state: Always run."):
            self.assertTrue(p(now=t, history=LastRunHistory()))

        with self.subTest("After a successful run: Wait for a day."):
            success_5_min_ago = LastRunHistory(
                entries=[
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=5)).timestamp(),
                        outcome="success",
                    )
                ]
            )
            self.assertFalse(p(now=t, history=success_5_min_ago))
            self.assertFalse(p(now=t + timedelta(hours=23), history=success_5_min_ago))
            self.assertTrue(p(now=t + timedelta(hours=25), history=success_5_min_ago))

        with self.subTest("After the first failed run: Wait for an hour."):
            failure_5_min_ago = LastRunHistory(
                entries=[
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=5)).timestamp(),
                        outcome="failure",
                    )
                ]
            )
            self.assertFalse(p(now=t, history=failure_5_min_ago))
            self.assertFalse(
                p(now=t + timedelta(minutes=55), history=failure_5_min_ago)
            )
            self.assertTrue(p(now=t + timedelta(minutes=65), history=failure_5_min_ago))

        with self.subTest(
            "After too many failed runs: Wait for a day after the last successful run."
        ):
            too_many_failures = LastRunHistory(
                entries=[
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=6)).timestamp(),
                        outcome="success",
                    ),
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=5)).timestamp(),
                        outcome="failure",
                        details="ugh",
                    ),
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=4)).timestamp(),
                        outcome="failure",
                        details="meh",
                    ),
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=3)).timestamp(),
                        outcome="failure",
                        details="boom",
                    ),
                ]
            )
            self.assertFalse(p(now=t, history=too_many_failures))
            self.assertFalse(p(now=t + timedelta(hours=22), history=too_many_failures))
            self.assertTrue(p(now=t + timedelta(hours=24), history=too_many_failures))

        with self.subTest(
            "Too many failed runs, and no previous successful run: Log an error, and don't run."
        ):
            too_many_failures_no_success = LastRunHistory(
                entries=[
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=5)).timestamp(),
                        outcome="failure",
                        details="ugh",
                    ),
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=4)).timestamp(),
                        outcome="failure",
                        details="meh",
                    ),
                    LastRunAttemptFinished(
                        at=(t - timedelta(minutes=3)).timestamp(),
                        outcome="failure",
                        details="boom",
                    ),
                ]
            )
            with self.assertLogs(level=ERROR):
                self.assertFalse(p(now=t, history=too_many_failures_no_success))

    def test_on_max_failures__stall(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=3,
            on_max_failures="stall",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunHistory(
            entries=[
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=6)).timestamp(),
                    outcome="success",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=5)).timestamp(),
                    outcome="failure",
                    details="ugh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=4)).timestamp(),
                    outcome="failure",
                    details="meh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=3)).timestamp(),
                    outcome="failure",
                    details="boom",
                ),
            ]
        )
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t, history=too_many_failures))
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t + timedelta(days=100), history=too_many_failures))

    def test_on_max_failures__ignore(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=1,
            on_max_failures="ignore",
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunHistory(
            entries=[
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=6)).timestamp(),
                    outcome="success",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=5)).timestamp(),
                    outcome="failure",
                    details="ugh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=4)).timestamp(),
                    outcome="failure",
                    details="meh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=3)).timestamp(),
                    outcome="failure",
                    details="boom",
                ),
            ]
        )
        self.assertFalse(p(now=t, history=too_many_failures))
        self.assertTrue(p(now=t + timedelta(hours=2), history=too_many_failures))

    def test_on_max_failures__invalid(self) -> None:
        p = RunPredicate(
            success_period=timedelta(days=1),
            neutral_period=timedelta(hours=2),
            failure_period=timedelta(hours=1),
            max_failures=1,
            on_max_failures="foo",  # type: ignore[arg-type]
        )

        t = datetime(2025, 1, 1, 12, 0, 0)

        too_many_failures = LastRunHistory(
            entries=[
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=6)).timestamp(),
                    outcome="success",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=5)).timestamp(),
                    outcome="failure",
                    details="ugh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=4)).timestamp(),
                    outcome="failure",
                    details="meh",
                ),
                LastRunAttemptFinished(
                    at=(t - timedelta(minutes=3)).timestamp(),
                    outcome="failure",
                    details="boom",
                ),
            ]
        )
        with self.assertLogs(level=ERROR):
            self.assertFalse(p(now=t, history=too_many_failures))
