from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from logging import getLogger
from pathlib import Path
from time import sleep
from typing import Literal, Callable, Any
from uuid import uuid4

from hcio_client import HealthChecks, HealthCheck

from ._last_run import create_run_predicate, LastRun
from ._rate_limiter import RateLimiter
from ._send_errors_to_slack import send_errors_to_slack

logger = getLogger(__name__)


def schedule(
    *,
    hc_ping_key: str | None = None,
    hc_manage_key: str | None = None,
    hc_timeout: timedelta | None = None,
    hc_grace: timedelta | None = None,
    hc_uuid: str | None = None,
    interval: timedelta,
    max_runs: int | None = None,
    heartbeat_path: Path | None = None,
    name: str | None = None,
    description: str | None = None,
    last_run_dir: Path,
    last_run_reset: bool = False,
    success_period: timedelta,
    failure_period: timedelta,
    max_failures: int,
    on_max_failures: Literal["ignore", "stall", "success_schedule"],
    func: Callable[[], None],
    slack_webhook: str | None = None,
    slack_rate_limiter: RateLimiter,
) -> None:
    """
    Schedule a function to run at a regular interval, with health checks and error reporting.

    Args:
        hc_ping_key:
            The ping key for `healthchecks.io`.
            To ping a health check, we need (the UUID) || (the slug && the ping key).
        hc_manage_key: The manage key for `healthchecks.io`.
        hc_timeout:
            The timeout for the health check.
            If a run takes longer than this, it will be marked as failed.
        hc_grace:
            The grace period for the health check.
            If a run fails, it will be given this much time to succeed before the next run is allowed to start.
        hc_uuid:
            The UUID of the health check.
            To ping a health check, we need (the UUID) || (the slug && the ping key).
        interval: The amount of time to wait between runs.
        max_runs: The maximum number of runs before exiting. If `None`, run indefinitely.
        heartbeat_path: If provided, this file will be touched at the start of each iteration to act as a heartbeat.
        name:
            A short name describing the task.
            Used as the slug for `healthchecks.io` and as the file name of the LastRun state.
            To ping a health check, we need (the UUID) || (the slug && the ping key).
        description: A description for the health check. Used for `healthchecks.io` and in Slack messages.
        last_run_dir: The directory in which to store the `LastRun` state files.
        last_run_reset: Whether to ignore the existing `LastRun` state files on the disk and start anew.
        success_period: The minimum time to wait after a successful run before allowing the next run.
        failure_period: The minimum time to wait after a failed run before allowing the next run.
        max_failures: The maximum number of consecutive failures before taking action based on `on_max_failures`.
        on_max_failures: What to do if the job failed more than `max_failures` times.
        func: The function to run at each interval.
        slack_webhook: If provided, errors will be posted to this Slack webhook URL.
        slack_rate_limiter: Rate limiter for posting messages to Slack.
    """

    hc = HealthChecks(
        ping_key=hc_ping_key,
        manage_key=hc_manage_key,
        create=True,
    )

    if hc_manage_key is None:
        if description is not None:
            logger.warning(
                "`description` is ignored when `hc_manage_key` is not provided."
            )
            description = None
        if hc_timeout is not None:
            logger.warning(
                "`hc_timeout` is ignored when `hc_manage_key` is not provided."
            )
            hc_timeout = None
        if hc_grace is not None:
            logger.warning(
                "`hc_grace` is ignored when `hc_manage_key` is not provided."
            )
            hc_grace = None

    check: HealthCheck | None = (
        hc.check(
            run_id=uuid4(),
            slug=name,
            uuid=hc_uuid,
            desc=description,
            timeout=int(hc_timeout.total_seconds()) if hc_timeout else None,
            grace=int(hc_grace.total_seconds()) if hc_grace else None,
        )
        # To ping a health check, we need (the UUID) || (the slug && the ping key).
        if (hc_uuid or (hc_ping_key and name))
        else None
    )

    predicate = create_run_predicate(
        success_period=success_period,
        failure_period=failure_period,
        max_failures=max_failures,
        on_max_failures=on_max_failures,
        check=check,
    )

    last_run_dir.mkdir(parents=True, exist_ok=True)
    last_run_path = last_run_dir / f"{name or uuid4()}.json"
    if last_run_reset:
        last_run_path.unlink(missing_ok=True)
    last_run = LastRun(path=last_run_path)

    def hc_func() -> None:
        with check or nullcontext():
            func()

    wrapped_function = last_run.wrap(f=hc_func, should_run=predicate)

    def attempt() -> None:
        if heartbeat_path is not None:
            logger.debug("Updating heartbeat …")
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.touch()

        with send_errors_to_slack(
            slack_webhook=slack_webhook,
            reraise=False,
            log_error=True,
            limiter=slack_rate_limiter,
        ):
            wrapped_function()

    repeat(
        func=attempt,
        max_runs=max_runs,
        sleep_duration=interval,
    )

    logger.info("Done.")


def repeat(
    func: Callable[..., Any],
    max_runs: int | None,
    sleep_duration: timedelta | None,
) -> None:
    """
    Run a function repeatedly with a sleep duration between runs. Stop on KeyboardInterrupt.

    Args:
        func: The function to run repeatedly.
        max_runs:
            The maximum number of runs before exiting.
            If `None`, run indefinitely.
            If less than 1, never run.
        sleep_duration:
            The amount of time to sleep between runs.
            If `None` or zero time, never sleep.
            Will not sleep before the first run or after the last run.
    """

    n_runs = 0

    def should_run() -> bool:
        return max_runs is None or n_runs < max_runs

    while should_run():
        try:
            func()
            n_runs += 1
        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt, stopping …")
            break

        if (
            should_run()
            and sleep_duration is not None
            and sleep_duration.total_seconds() > 0
        ):
            logger.debug(f"Sleeping for {sleep_duration} …")
            sleep(sleep_duration.total_seconds())
