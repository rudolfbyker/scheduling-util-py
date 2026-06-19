import sys
from logging import getLogger

import click
from click import ClickException

logger = getLogger(__name__)


@click.command()
@click.option("--name", default="world")
def hello(
    *,
    name: str,
) -> None:
    """
    Log a greeting.
    """
    logger.info(f"Hello {name}!")


@click.command()
@click.option("--code", default=0, type=int)
def exit_code(*, code: int) -> None:
    """
    Exit with the given code.
    """
    sys.exit(code)


@click.command()
@click.option("--message", default="meh")
def raise_exception(*, message: str) -> None:
    """
    Raise an exception with the given message.
    """
    raise ClickException(message)
