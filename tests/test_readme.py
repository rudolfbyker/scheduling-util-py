import doctest
import unittest
from pathlib import Path
from typing import Any
from unittest import BaseTestSuite

repo_dir = Path(__file__).parent.parent.resolve()


# noinspection PyUnusedLocal
def load_tests(loader: Any, tests: BaseTestSuite, ignore: Any) -> BaseTestSuite:
    """
    See https://docs.python.org/3/library/doctest.html#unittest-api
    """
    tests.addTests(
        doctest.DocFileSuite(
            (repo_dir / "README.md").as_posix(),
            module_relative=False,
        )
    )
    return tests


if __name__ == "__main__":
    unittest.main(
        failfast=True,
    )
