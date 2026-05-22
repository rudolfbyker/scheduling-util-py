from __future__ import annotations

import sys
from logging import getLevelName, WARNING, DEBUG, INFO, ERROR, CRITICAL
from pathlib import Path

import click
from click_pendulum import Duration

click_type__log_level = click.Choice(
    [
        getLevelName(DEBUG),
        getLevelName(INFO),
        getLevelName(WARNING),
        getLevelName(ERROR),
        getLevelName(CRITICAL),
    ],
    case_sensitive=False,
)

click_option__log_level = click.option(
    "--log-level",
    default=getLevelName(WARNING),
    show_default=True,
    type=click_type__log_level,
    help="The level of logging for the `stderr` output. DEBUG is the most verbose, and CRITICAL is the least verbose.",
)


def get_default_cache_dir() -> Path:
    """
    Get the default cache directory.
    """
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "scheduling_util" / "cache"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "scheduling_util"
    else:
        return Path.home() / ".cache" / "scheduling_util"


click_option__cache_dir = click.option(
    "--cache-dir",
    default=get_default_cache_dir().resolve().as_posix(),
    show_default=True,
    type=click.Path(
        file_okay=False,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    help="Directory for cache files.",
)

what_is_needed_to_ping = (
    "To ping a health check, we need (the UUID) || (the slug && the ping key)."
)

click_option__hc_ping_key = click.option(
    "--hc-ping-key",
    required=False,
    help=f"The `healthchecks.io` ping key. {what_is_needed_to_ping}",
)

click_option__hc_manage_key = click.option(
    "--hc-manage-key",
    required=False,
    help="The `healthchecks.io` manage key.",
)

click_option__hc_timeout = click.option(
    "--hc-timeout",
    required=False,
    type=Duration(),
    help="The timeout for the health check. Examples: `5m`, `1h`, `2d`, `1w`.",
)

click_option__hc_grace = click.option(
    "--hc-grace",
    required=False,
    type=Duration(),
    help="The grace period for the health check. Examples: `5m`, `1h`, `2d`, `1w`. Should be longer than the timeout.",
)

click_option__hc_uuid = click.option(
    "--hc-uuid",
    required=False,
    type=click.UUID,
    help=f"The UUID of the health check. {what_is_needed_to_ping}",
)

click_option__heartbeat_file = click.option(
    "--heartbeat-file",
    type=click.Path(
        dir_okay=False,
        writable=True,
        path_type=Path,
        resolve_path=True,
    ),
    required=False,
    help="Path to a file that will be touched on the start of each poll iteration to act as a heartbeat. "
    "This allows health check scripts to know when this script stopped running or got stuck.",
)
