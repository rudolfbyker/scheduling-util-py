import time
from datetime import datetime, timedelta
from functools import (
    WRAPPER_ASSIGNMENTS,
    WRAPPER_UPDATES,
    update_wrapper,
)
from logging import getLogger
from pathlib import Path
from typing import (
    TypeVar,
    ParamSpec,
    Callable,
    Any,
    Protocol,
    Literal,
    Optional,
    Tuple,
)

from hcio_client import HealthCheck

from scheduling_util._last_run_history import (
    LastRunHistory,
    LastRunAttemptStarted,
    LastRunAttemptFinished,
)
from scheduling_util._types import Outcome

logger = getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


class ShouldRunCallable(Protocol):
    def __call__(
        self,
        *,
        now: datetime,
        history: LastRunHistory,
    ) -> bool: ...


class AssessResultCallable(Protocol):
    def __call__(
        self,
        *,
        returned: R | None,
        raised: BaseException | None,
    ) -> Tuple[Outcome, str]: ...


# noinspection PyUnusedLocal
def default_assess_result(
    *,
    returned: R | None,
    raised: BaseException | None,
) -> Tuple[Outcome, str]:
    outcome: Outcome
    if raised:
        details = f"Raised `{type(raised).__name__}`: {raised}"
        outcome = "failure"
    else:
        details = ""
        outcome = "success"
    return outcome, details


class LastRun:
    """
    Keep track of when a script last ran, succeeded, failed, etc. using a JSON file.

    This, combined with a predicate function, allows scheduling multiple jobs to run at specific intervals,
    e.g., every week, while the main script itself runs more frequently, e.g., every 10 minutes,
    while also preventing failed jobs from retrying too often.
    """

    _name: str

    _history: LastRunHistory

    def __init__(
        self,
        *,
        path: Path,
        name: str | None = None,
        max_history_entries: int = 1000,
    ) -> None:
        """
        Args:
            path: The JSON file where the history of attempts is to be stored and read from.
            name:
                The name of the job that is being run. Only used for logging.
                If omitted, the stem of the file name is used.
            max_history_entries:
                The maximum number of history entries to keep.
                A completed attempt normally adds two entries: one "started" entry and one "finished" entry.
        """
        if path.exists() and not path.is_file():
            raise ValueError(f"{path} exists but is not a file.")

        path.parent.mkdir(parents=True, exist_ok=True)

        self._name = name or path.stem
        self._history = LastRunHistory(max_entries=max_history_entries, path=path)

    @property
    def name(self) -> str:
        return self._name

    @property
    def history(self) -> LastRunHistory:
        return self._history

    def wrap(
        self,
        *,
        f: Callable[P, R],
        should_run: ShouldRunCallable,
        assess_result: AssessResultCallable = default_assess_result,
    ) -> Callable[P, R | None]:
        """
        Get a function that only runs if it has not run too recently.

        I wanted to use a context manager for this, but it's not possible to skip the `with` block.
        See https://peps.python.org/pep-0377/

        The original function signature is preserved, but the return type is changed from `R` to `R | None`.
        `None` can be returned when:
        - The function call was skipped.
        - The function raised an exception which was then suppressed by the `assess_result` function.

        Args:
            f: The function to wrap.
            should_run: A predicate function that decides whether the function should run at a given time.
            assess_result:
                A predicate function that decides whether the function call was successful
                and provides a string with extra details, such as a reason or an error message.
                By default, any function that does not raise an exception is considered successful.
                If the function raised an exception, returning "neutral" or "success" will suppress it.
                You probably should not return "neutral" or "success" for things like `KeyboardInterrupt`.
        """

        def wrapper(*args: Any, **kwargs: Any) -> R | None:
            logger.debug(f"[{self._name}] Checking if it should run …")
            if not should_run(now=datetime.now(), history=self.history):
                logger.debug(f"[{self._name}] Skipped.")
                return None

            logger.debug(f"[{self._name}] Started.")
            self.history.append(LastRunAttemptStarted(at=time.time()))

            try:
                func_result = f(*args, **kwargs)
            except BaseException as e:
                logger.debug(f"[{self._name}] Raised `%s`: %s", type(e).__name__, e)
                outcome, details = assess_result(returned=None, raised=e)
                if outcome == "failure":
                    raise e
                # Reaching this means that the exception was suppressed because the `assess_result`
                # function returned "success" or "neutral".
                func_result = None
            else:
                logger.debug(f"[{self._name}] Returned `%s`.", str(func_result)[:100])
                outcome, details = assess_result(returned=func_result, raised=None)
            finally:
                self.history.append(
                    LastRunAttemptFinished(
                        at=time.time(),
                        outcome=outcome,
                        details=details,
                    )
                )

            return func_result

        # Make the wrapper look exactly like the original function, except for the return type.
        wrap_result = update_wrapper(
            wrapper=wrapper,
            wrapped=f,
            assigned=WRAPPER_ASSIGNMENTS,
            updated=WRAPPER_UPDATES,
        )
        # noinspection PyTypeHints,PyUnresolvedReferences
        wrap_result.__annotations__["return"] = Optional[
            f.__annotations__.get("return")
        ]
        return wrap_result


class _Unset:
    """
    Sentinel type for the absence of a forced predicate return value.
    """

    pass


_UNSET = _Unset()


def create_run_predicate(
    *,
    success_period: timedelta,
    neutral_period: timedelta,
    failure_period: timedelta,
    max_failures: int,
    on_max_failures: Literal["ignore", "stall", "success_schedule"],
    check: HealthCheck | None = None,
) -> ShouldRunCallable:
    """
    Factory function for backward compatibility.
    """
    return RunPredicate(
        success_period=success_period,
        neutral_period=neutral_period,
        failure_period=failure_period,
        max_failures=max_failures,
        on_max_failures=on_max_failures,
        check=check,
    )


class RunPredicate:
    """
    Decide whether a job may run based on last-run history and failure policy.
    """

    def __init__(
        self,
        *,
        success_period: timedelta,
        neutral_period: timedelta,
        failure_period: timedelta,
        max_failures: int,
        on_max_failures: Literal["ignore", "stall", "success_schedule"],
        check: HealthCheck | None = None,
    ) -> None:
        """
        Create a predicate function for `LastRun.wrap`.

        Args:
            success_period:
                The minimum time to wait after a successful run.
                E.g., if you typically want a job to run every 5 days, set this to 5 days.
            neutral_period:
                The minimum time to wait after a neutral (i.e., neither successful nor failed) run.
                E.g., a script might know that it has to wait for a specific thing to happen before it can succeed.
                This time might be longer or shorter than either `success_period` or `failure_period`.
            failure_period:
                The minimum time to wait after a failed run.
                E.g., if you want to retry a failed job sooner, instead of waiting for the next schedule,
                make this smaller than `success_period`.
            max_failures:
                The maximum number of failures (since the last success) before taking an action based on `on_max_failures`.
            on_max_failures:
                What to do if the job failed more than `max_failures` times (since the last success):

                    - `ignore`: Keep retrying indefinitely. `max_failures` is ignored.
                    - `stall`: Human intervention is required before the job will run again.
                    - `success_schedule`: Run again `success_period` after the last successful run, if there is one.
                      This is only useful if `success_period` is larger than `failure_period`.
            check:
                A `HealthCheck` instance to use for reporting failures.
                If `None`, no health check reporting will be done.
        """

        self.success_period = success_period
        self.neutral_period = neutral_period
        self.failure_period = failure_period
        self.max_failures = max_failures
        self.on_max_failures = on_max_failures
        self.check = check

        self._next_return_value: bool | _Unset = _UNSET

    def __call__(
        self,
        *,
        now: datetime,
        history: LastRunHistory,
    ) -> bool:
        """
        Return whether a job may run for the supplied history snapshot.
        """

        if self._next_return_value is not _UNSET:
            result = self._next_return_value
            self._next_return_value = _UNSET
            assert isinstance(result, bool)
            return result

        return self._predicate(now=now, history=history)

    def force_next_return_value(self, value: bool) -> None:
        """
        Force the next predicate call to return the supplied value once.
        """

        self._next_return_value = value

    def unset_next_return_value(self) -> None:
        """
        Clear any pending forced predicate return value.
        """

        self._next_return_value = _UNSET

    def _report_error(self, message: str) -> None:
        """
        Log and report a scheduling policy error to `healthchecks.io` if configured.
        """

        logger.error(message)
        if self.check is not None:
            self.check.ping_log(data=message)
            self.check.ping_failure()

    def _predicate(self, *, now: datetime, history: LastRunHistory) -> bool:
        """
        Evaluate the normal last-run-history scheduling policy.
        """

        with history.lock():
            last_finished = history.last_finished

            if last_finished is None:
                # The first run.
                return True

            if last_finished.outcome == "success":
                # The previous attempt was successful. Follow the success schedule.
                return now - last_finished.datetime > self.success_period

            if last_finished.outcome == "neutral":
                # The previous attempt was neutral. Follow the neutral schedule.
                return now - last_finished.datetime > self.neutral_period

            if (
                history.n_failures_since_last_success < self.max_failures
                or self.on_max_failures == "ignore"
            ):
                # The previous attempt failed, but not too many times.
                return now - last_finished.datetime > self.failure_period

            # The job failed too many times.
            last_finished_json = last_finished.model_dump_json(indent=2)
            last_success = history.last_success

            if self.on_max_failures == "stall" or last_success is None:
                self._report_error(
                    f"Job stalled after {self.max_failures} tries. The last failure was:\n{last_finished_json}"
                )
                return False

            if self.on_max_failures == "success_schedule":
                # Run again `success_period` after the last successful run, if there is one.
                return now - last_success.datetime > self.success_period

            self._report_error("Unexpected state.")
            return False
