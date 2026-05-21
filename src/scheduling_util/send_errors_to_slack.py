import json
import traceback
from contextlib import contextmanager
from logging import getLogger
from typing import Generator, Any

from requests import post

from .rate_limiter import RateLimiter

logger = getLogger(__name__)


@contextmanager
def send_errors_to_slack(
    *,
    slack_webhook: str | None,
    reraise: bool,
    limiter: RateLimiter,
    log_error: bool,
) -> Generator[None, None, None]:
    """
    A context manager: If the context fails, post to Slack.

    Args:
        slack_webhook:
            The slack webhook to post to.
            If this is `None`, the context manager becomes a no-op.
        reraise:
            Whether to reraise the error or not.
            If this is `False`, the context manager acts as an error boundary.
        limiter:
            A rate limiter to prevent hitting Slack's rate limits.
        log_error:
            Whether to also send the error to the default logger.
    """

    try:
        yield
    except Exception as e:
        if log_error:
            logger.error(e)

        if slack_webhook is not None:
            if limiter.ping_if_ready():
                try:
                    logger.debug("Posting error to Slack.")
                    post(
                        url=slack_webhook,
                        data=json.dumps(error_to_slack(e=e)),
                        timeout=30,
                    )
                except Exception as e:
                    logger.error(e)
            else:
                logger.warning(
                    "Not posting to Slack to avoid hitting rate limits. "
                    f"Last post was at {limiter.timestamp.isoformat()}."
                )

        if reraise:
            raise


def error_to_slack(*, e: BaseException) -> dict[str, Any]:
    """
    Convert a Python exception to a Slack message.
    """
    text_block = {
        "type": "section",
        "block_id": "text_block",
        "text": {
            "type": "mrkdwn",
            "text": f"`{e.__class__.__name__}`:\n```\n{e}\n```",
        },
    }

    traceback_text = (
        "".join(traceback.format_tb(e.__traceback__))
        if e.__traceback__ is not None
        else "No traceback available."
    )

    traceback_block = {
        "type": "section",
        "block_id": "traceback_block",
        "text": {
            "type": "mrkdwn",
            "text": "```\n" + traceback_text + "\n```",
        },
    }

    return {
        "blocks": [
            text_block,
            traceback_block,
        ]
    }
