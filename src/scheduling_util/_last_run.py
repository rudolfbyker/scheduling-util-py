import json
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
    TypedDict,
    Protocol,
    Literal,
    Dict,
    Optional,
)

from hcio_client import HealthCheck

logger = getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


class LastRunState(TypedDict):
    """
    All information that might influence whether a job should run at a given time.
    """

    last_attempted: datetime | None
    """
    The start time of the last attempt.
    """

    last_successful: datetime | None
    """
    The end time of the last successful attempt.
    """

    last_failed: datetime | None
    """
    The end time of the last failed attempt.
    """

    n_consecutive_failures: int
    """
    The number of consecutive failures.
    """

    last_failure: BaseException | str | None
    """
    The exception or error message of the last failed attempt.
    """


class ShouldRunCallable(Protocol):
    def __call__(self, *, now: datetime, state: LastRunState) -> bool: ...


class LastRun:
    """
    Keep track of when a script last ran, succeeded, failed, etc. using a JSON file.

    This, combined with a predicate function, allows scheduling multiple jobs to run at specific intervals,
    e.g., every week, while the main script itself runs more frequently, e.g., every 10 minutes,
    while also preventing failed jobs from retrying too often.
    """

    _path: Path
    _name: str

    _state: LastRunState

    def __init__(
        self,
        *,
        path: Path,
        name: str | None = None,
    ) -> None:
        """
        Args:
            path:
                The file where the details about the last runs are to be stored and read from.
                This will be a JSON file.
            name:
                The name of the job that is being run. Only used for logging.
                If omitted, the stem of the file name is used.
        """
        if path.exists() and not path.is_file():
            raise ValueError(f"{path} exists but is not a file.")

        path.parent.mkdir(parents=True, exist_ok=True)

        self._path = path
        self._name = name or path.stem
        self._state = read_state(path=path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> LastRunState:
        return self._state

    @state.setter
    def state(self, value: LastRunState) -> None:
        self._state = value
        write_state(path=self.path, state=value)

    def set_attempted(self) -> None:
        self.state = {**self.state, "last_attempted": datetime.now()}

    def set_successful(self) -> None:
        self.state = {
            **self.state,
            "last_successful": datetime.now(),
            "n_consecutive_failures": 0,
        }

    def set_failed(self, failure: str | BaseException) -> None:
        self.state = {
            **self.state,
            "last_failed": datetime.now(),
            "n_consecutive_failures": self.state["n_consecutive_failures"] + 1,
            "last_failure": failure,
        }

    def wrap(
        self,
        *,
        f: Callable[P, R],
        should_run: ShouldRunCallable,
    ) -> Callable[P, R | None]:
        """
        Get a function that only runs if it has not run too recently.

        I wanted to use a context manager for this, but it's not possible to skip the `with` block.
        See https://peps.python.org/pep-0377/

        The original function signature is preserved, but the return type is changed from `R` to `R | None`,
        because it will return `None` if the function call was skipped.

        Args:
            f: The function to wrap.
            should_run: A predicate function that decides whether the function should run at a given time.
        """

        def wrapper(*args: Any, **kwargs: Any) -> R | None:
            logger.debug(f"[{self._name}] Checking if it should run …")
            if not should_run(now=datetime.now(), state=self.state):
                logger.debug(f"[{self._name}] Skipped.")
                return None

            logger.debug(f"[{self._name}] Started.")
            self.set_attempted()

            try:
                result = f(*args, **kwargs)
            except BaseException as e:
                logger.debug(f"[{self._name}] Failed: {e}")
                self.set_failed(failure=e)
                raise e
            else:
                logger.debug(f"[{self._name}] Succeeded.")
                self.set_successful()

            return result

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


def state_to_json_serializable(*, state: LastRunState) -> Dict[str, Any]:
    return {
        "last_attempted": encode_optional_iso_date(state["last_attempted"]),
        "last_successful": encode_optional_iso_date(state["last_successful"]),
        "last_failed": encode_optional_iso_date(state["last_failed"]),
        "n_consecutive_failures": state["n_consecutive_failures"],
        "last_failure": (
            None if state["last_failure"] is None else str(state["last_failure"])
        ),
    }


def write_state(*, path: Path, state: LastRunState) -> None:
    with path.open("w") as f:
        json.dump(
            obj=state_to_json_serializable(state=state),
            fp=f,
            indent=2,
        )


def read_state(*, path: Path) -> LastRunState:
    try:
        with path.open("r") as f:
            data = json.load(f)
            return LastRunState(
                last_attempted=parse_optional_iso_date(data.get("last_attempted")),
                last_successful=parse_optional_iso_date(data.get("last_successful")),
                last_failed=parse_optional_iso_date(data.get("last_failed")),
                n_consecutive_failures=data.get("n_consecutive_failures", 0),
                last_failure=data.get("last_failure"),
            )
    except FileNotFoundError:
        # Default, empty state.
        return LastRunState(
            last_attempted=None,
            last_successful=None,
            last_failed=None,
            n_consecutive_failures=0,
            last_failure=None,
        )


def parse_optional_iso_date(data: str | None) -> datetime | None:
    if data is None:
        return None
    return datetime.fromisoformat(data)


def encode_optional_iso_date(data: datetime | None) -> str | None:
    if data is None:
        return None
    return data.isoformat()


def create_run_predicate(
    *,
    success_period: timedelta,
    failure_period: timedelta,
    max_failures: int,
    on_max_failures: Literal["ignore", "stall", "success_schedule"],
    check: HealthCheck | None = None,
) -> ShouldRunCallable:
    """
    Create a predicate function for `LastRun.wrap`.

    Args:
        success_period:
            The minimum time to wait after a successful run.
            E.g., if you typically want a job to run every 5 days, set this to 5 days.
        failure_period:
            The minimum time to wait after a failed run.
            E.g., if you want to retry a failed job sooner, instead of waiting for the next schedule,
            make this smaller than `success_period`.
        max_failures:
            The maximum number of consecutive failures.
        on_max_failures:
            What to do if the job failed more than `max_failures` times.

                - "ignore": Keep retrying indefinitely. `max_failures` is ignored.
                - "stall": Human intervention is required before the job will run again.
                - "success_period": Run again `success_period` after the last successful run, if there is one.
                  This is only useful if `success_period` is larger than `failure_period`.
        check:
            A `HealthCheck` instance to use for reporting failures.
            If `None`, no health check reporting will be done.
    """

    def report_error(message: str) -> None:
        logger.error(message)
        if check is not None:
            check.ping_log(data=message)
            check.ping_failure()

    def predicate(*, now: datetime, state: LastRunState) -> bool:
        """
        Predicate function for `LastRun.wrap`.
        """
        if state["n_consecutive_failures"] < 1:
            if state["last_successful"] is None:
                # The first run.
                return True

            # The previous attempt was successful. Follow the schedule.
            return now - state["last_successful"] > success_period

        elif (
            state["n_consecutive_failures"] < max_failures
            or on_max_failures == "ignore"
        ):
            if state["last_failed"] is None:
                # There was a previous failure, but we don't know when.
                # This state should be impossible.
                report_error(
                    f"The last failed time is missing:\n{json.dumps(obj=state_to_json_serializable(state=state), indent=2)}"
                )
                return False

            # The previous attempt failed, but not too many times.
            return now - state["last_failed"] > failure_period

        # The job failed too many times.
        match on_max_failures:

            case "stall":
                report_error(
                    f"Job stalled after {max_failures} tries:\n{state['last_failure']}"
                )
                return False

            case "success_schedule":
                if state["last_successful"] is None:
                    report_error(
                        f"Job stalled after {max_failures} tries:\n{state['last_failure']}"
                    )
                    return False

                # Run again `success_period` after the last successful run, if there is one.
                return now - state["last_successful"] > success_period

        report_error(
            f"Don't know what to do with state:\n{json.dumps(obj=state_to_json_serializable(state=state), indent=2)}"
        )
        return False

    return predicate
