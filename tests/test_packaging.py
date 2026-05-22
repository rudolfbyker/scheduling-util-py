import re
import tomllib
import unittest
from pathlib import Path

repo_dir = Path(__file__).resolve().parent.parent


class TestPackaging(unittest.TestCase):

    def test_requests__runtime_dependency(self) -> None:
        pyproject = tomllib.loads(
            (repo_dir / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies = pyproject["project"]["dependencies"]
        dependency_names = {
            re.split(r"\s|@|[<>=!~\[]", dependency, maxsplit=1)[0].casefold()
            for dependency in dependencies
        }

        self.assertIn("requests", dependency_names)
