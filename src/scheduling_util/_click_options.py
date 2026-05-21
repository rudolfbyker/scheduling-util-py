from __future__ import annotations

from logging import getLevelName, WARNING, DEBUG, INFO, ERROR, CRITICAL
from pathlib import Path

import click
from click_pendulum import Duration

click_option__log_level = click.option(
    "--log-level",
    default=getLevelName(WARNING),
    type=click.Choice(
        [
            getLevelName(DEBUG),
            getLevelName(INFO),
            getLevelName(WARNING),
            getLevelName(ERROR),
            getLevelName(CRITICAL),
        ],
        case_sensitive=False,
    ),
    show_default=True,
    help="The level of logging for the `stderr` output. DEBUG is the most verbose, and CRITICAL is the least verbose.",
)

click_option__cache_dir = click.option(
    "--cache-dir",
    required=True,
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    help="Directory for cache files (e.g., cloned git repos) persistent between backup runs.",
)

click_option__hc_ping_key = click.option(
    "--hc-ping-key",
    required=True,
    help="The `healthchecks.io` ping key.",
)

click_option__hc_manage_key = click.option(
    "--hc-manage-key",
    required=True,
    help="The `healthchecks.io` manage key.",
)

click_option__hc_timeout = click.option(
    "--hc-timeout",
    required=True,
    type=Duration(),
    help="The timeout for the health check. Examples: `5m`, `1h`, `2d`, `1w`.",
)

click_option__hc_grace = click.option(
    "--hc-grace",
    required=True,
    type=Duration(),
    help="The grace period for the health check. Examples: `5m`, `1h`, `2d`, `1w`. Should be longer than the timeout.",
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
