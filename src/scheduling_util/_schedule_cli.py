from __future__ import annotations

from logging import getLogger, INFO
from pathlib import Path
from typing import Literal

import click
from click_pendulum import Duration as ClickDuration
from pendulum import Duration
from run_with_logger import run_with_logger

from ._click_options import (
    click_option__cache_dir,
    click_option__hc_ping_key,
    click_option__hc_manage_key,
    click_option__log_level,
    click_option__hc_grace,
    click_option__hc_timeout,
    click_option__heartbeat_file,
)
from ._logging_util import stream_logs_to_stderr
from ._rate_limiter import RateLimiter
from ._schedule import schedule

logger = getLogger(__name__)


@click.command()
@click_option__log_level
@click_option__hc_ping_key
@click_option__hc_manage_key
@click_option__hc_timeout
@click_option__hc_grace
@click.option(
    "--interval",
    type=ClickDuration(),
    default="1m",
    show_default=True,
    help="Run this script continually, waiting this amount of time between runs.",
)
@click.option(
    "--max-runs",
    type=int,
    required=False,
    help="The maximum number of iterations of this script before exiting. "
    "If not set, this script will run indefinitely.",
)
@click_option__heartbeat_file
@click.option(
    "--slug",
    required=True,
    help="A short name describing the task. "
    "Used as the slug for `healthchecks.io` and as the file name of the LastRun state.",
)
@click.option(
    "--description",
    required=False,
    help="A description for the health check. "
    "Used for `healthchecks.io` and in Slack messages.",
)
@click_option__cache_dir
@click.option(
    "--success-period",
    default="1d",
    show_default=True,
    type=ClickDuration(),
    help=(
        "The minimum time to wait after a successful run. "
        "E.g., if you typically want a job to run every 5 days, set this to 5d. "
        "Examples: `5m`, `1h`, `2d`, `1w`."
    ),
)
@click.option(
    "--failure-period",
    default="1d",
    show_default=True,
    type=ClickDuration(),
    help=(
        "The minimum time to wait after a failed run. "
        "E.g., if you want to retry a failed job sooner instead of waiting for the next schedule, "
        "make this smaller than `--success-period`. Examples: `5m`, `1h`, `2d`, `1w`."
    ),
)
@click.option(
    "--max-failures",
    default=3,
    show_default=True,
    type=int,
    help="The maximum number of consecutive failures.",
)
@click.option(
    "--on-max-failures",
    default="stall",
    show_default=True,
    type=click.Choice(["ignore", "stall", "success_schedule"], case_sensitive=True),
    help=(
        "What to do if the job failed more than `--max-failures` times. "
        "\n  - ignore: Keep retrying indefinitely (ignore `--max-failures`). "
        "\n  - stall: Require human intervention before the job runs again. "
        "\n  - success_schedule: Run again `--success-period` after the last successful run (if any)."
    ),
)
@click.option(
    "--command",
    required=False,
    default=None,
    help="The shell command to run. "
    "This will be passed directly to `subprocess.run` with `shell=True`.",
)
@click.option(
    "--py-exec",
    required=False,
    default=None,
    help="The Python code to run. "
    "This will be passed directly to `exec`. "
    "The code will run in the same process as this script.",
)
@click.option(
    "--slack-webhook",
    required=False,
    default=None,
    help="If provided, errors will be posted to this Slack webhook URL.",
)
@click.option(
    "--slack-rate-limit",
    required=False,
    type=ClickDuration(),
    default="1h",
    show_default=True,
    help="If `--slack-webhook` is provided, this option specifies the minimum amount of time to wait "
    "between posting messages to Slack to avoid hitting rate limits.",
)
def schedule_cli(
    *,
    log_level: str,
    hc_ping_key: str | None,
    hc_manage_key: str | None,
    hc_timeout: Duration,
    hc_grace: Duration,
    interval: Duration,
    max_runs: int | None,
    heartbeat_file: Path | None,
    slug: str,
    description: str | None,
    cache_dir: Path,
    success_period: Duration,
    failure_period: Duration,
    max_failures: int,
    on_max_failures: Literal["ignore", "stall", "success_schedule"],
    command: str | None,
    py_exec: str | None,
    slack_webhook: str | None,
    slack_rate_limit: Duration,
) -> None:
    """
    This script runs as a daemon, executing a command or Python code at a regular interval,
    and reporting the results to `healthchecks.io`.
    """
    if command is None and py_exec is None:
        raise click.UsageError("Either --command or --py-exec must be provided.")
    if command is not None and py_exec is not None:
        raise click.UsageError("Only one of --command or --py-exec may be provided.")

    def func() -> None:
        if command is not None:
            run_with_logger(
                args=command,
                shell=True,
                logger=logger,
                level=INFO,
                check=True,
            )
        elif py_exec is not None:
            exec(py_exec)

    stream_logs_to_stderr(log_level=log_level)
    slack_rate_limiter = RateLimiter(
        minimum_period=slack_rate_limit,
        path=cache_dir / "rate_limiter" / "slack_errors",
    )

    schedule(
        hc_ping_key=hc_ping_key,
        hc_manage_key=hc_manage_key,
        hc_timeout=hc_timeout,
        hc_grace=hc_grace,
        interval=interval,
        max_runs=max_runs,
        heartbeat_file=heartbeat_file,
        slug=slug,
        description=description,
        last_run_dir=cache_dir / "last_run",
        success_period=success_period,
        failure_period=failure_period,
        max_failures=max_failures,
        on_max_failures=on_max_failures,
        func=func,
        slack_webhook=slack_webhook,
        slack_rate_limiter=slack_rate_limiter,
    )
