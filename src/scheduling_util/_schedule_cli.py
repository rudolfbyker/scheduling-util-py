from __future__ import annotations

from importlib import import_module
from logging import getLogger, INFO, getLevelName, getLevelNamesMapping
from pathlib import Path
from typing import Literal, Callable, Tuple, Any

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
    click_type__log_level,
)
from ._logging_util import stream_logs_to_stderr
from ._rate_limiter import RateLimiter
from ._schedule import schedule

logger = getLogger(__name__)


@click.group()
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
@click.option(
    "--reset",
    is_flag=True,
    default=False,
    help="Delete the state of the last run from the disk before starting, "
    "so that the first run will always happen immediately. "
    "Useful for testing.",
)
def schedule_cli(
    *,
    log_level: str,
    **_kwargs: Any,
) -> None:
    """
    This script runs as a daemon, executing a command or Python code at a regular interval,
    and reporting the results to `healthchecks.io`.
    """
    stream_logs_to_stderr(log_level=log_level)


@schedule_cli.command(
    context_settings=dict(
        ignore_unknown_options=True,
    )
)
@click.option(
    "--command",
    "command_str",
    required=True,
    help="The Click command to import and invoke. "
    "Use the same syntax as you would in the `project.scripts` section of `pyproject.toml`.",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def click_invoke(
    *,
    command_str: str,
    args: Tuple[str],
) -> Callable[[], None]:
    """
    Schedule another Click command.
    Run it in the same process using `click.Context.invoke`.
    """
    if ":" not in command_str:
        raise ValueError(
            f"Command string must be in format 'module.path:function_name', got: {command_str}"
        )

    try:
        module_path, function_name = command_str.rsplit(":", 1)
        module = import_module(module_path)
        command = getattr(module, function_name)
    except Exception as e:
        raise ValueError(f"Failed to find Click command `{command_str}`.") from e

    if not isinstance(command, click.Command):
        raise TypeError(
            f"`{command_str}` is not a Click command, but `{type(command).__name__}`."
        )

    def func() -> None:
        with command.make_context(
            command.name,
            list(args),
            parent=None,  # Don't make our command the parent of this command, to keep things separated.
        ) as command_context:
            command.invoke(command_context)

    return func


@schedule_cli.command(
    context_settings=dict(
        ignore_unknown_options=True,
    )
)
@click.option(
    "--code",
    required=True,
    help="The Python code to run. " "This will be passed directly to `exec`. ",
)
def py_exec(
    *,
    code: str,
) -> Callable[[], None]:
    """
    Schedule Python code.

    The code will run in the same process as this script.
    """

    def func() -> None:
        exec(code)

    return func


@schedule_cli.command(
    context_settings=dict(
        ignore_unknown_options=True,
    )
)
@click.option(
    "--shell/--no-shell",
    default=False,
    show_default=True,
    help="Whether to call `subprocess.run` with `shell=True`",
)
@click.option(
    "--check/--no-check",
    default=True,
    show_default=True,
    help="Whether to call `subprocess.run` with `check=True`",
)
@click.option(
    "--log-level",
    default=getLevelName(INFO),
    show_default=True,
    type=click_type__log_level,
    help="On which log level to write the `stdout` and `stderr` streams received from the subprocess.",
)
@click.argument(
    "args",
    nargs=-1,
    type=click.UNPROCESSED,
)
def subprocess(
    *,
    shell: bool,
    check: bool,
    log_level: str,
    args: Tuple[str],
) -> Callable[[], None]:
    """
    Schedule a shell command.
    Run it in a subprocess using `run_with_logger` (which uses `subprocess.run`).

    ARGS: The arguments to pass to the subprocess.
    """

    def func() -> None:
        run_with_logger(
            args=args,
            shell=shell,
            logger=logger,
            level=getLevelNamesMapping().get(log_level, INFO),
            check=check,
        )

    return func


@schedule_cli.result_callback()
def schedule_cli__on_result(
    func: Callable[[], None],
    *,
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
    slack_webhook: str | None,
    slack_rate_limit: Duration,
    reset: bool,
    **_kwargs: Any,
) -> None:
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
        last_run_reset=reset,
        success_period=success_period,
        failure_period=failure_period,
        max_failures=max_failures,
        on_max_failures=on_max_failures,
        func=func,
        slack_webhook=slack_webhook,
        slack_rate_limiter=RateLimiter(
            minimum_period=slack_rate_limit,
            path=cache_dir / "rate_limiter" / "slack_errors",
        ),
    )
