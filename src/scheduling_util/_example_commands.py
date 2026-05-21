from logging import getLogger

import click

logger = getLogger(__name__)


@click.command()
@click.option("--name", default="world")
def hello(
    *,
    name: str,
) -> None:
    logger.info(f"Hello {name}!")
