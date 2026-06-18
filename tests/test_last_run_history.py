import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scheduling_util._last_run_history import (
    LastRunAttemptFinished,
    LastRunAttemptStarted,
    LastRunHistory,
)

LOGGER_NAME = "scheduling_util._last_run_history"


class TestLastRunHistory(unittest.TestCase):
    def test_missing_file_starts_empty(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            history = LastRunHistory(path=Path(tmp_dir_str) / "last-run.json")
            entries = history.entries

        self.assertEqual([], entries)

    def test_round_trips_entries_through_json(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            history = LastRunHistory(path=path)

            history.append(LastRunAttemptStarted(at=1))
            history.append(
                LastRunAttemptFinished(
                    at=2,
                    outcome="failure",
                    details="boom",
                )
            )

            persisted = json.loads(path.read_text())
            reread = LastRunHistory(path=path)
            entries = reread.entries
            last_failure = reread.last_failure

        self.assertEqual(
            [
                {"at": 1.0, "kind": "started"},
                {
                    "at": 2.0,
                    "kind": "finished",
                    "outcome": "failure",
                    "details": "boom",
                },
            ],
            persisted,
        )
        self.assertIsInstance(entries[0], LastRunAttemptStarted)
        self.assertIsInstance(entries[1], LastRunAttemptFinished)
        assert isinstance(last_failure, LastRunAttemptFinished)
        self.assertEqual("boom", last_failure.details)
        self.assertEqual("failure", last_failure.outcome)

    def test_invalid_json_logs_and_starts_clean(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text("{not json")

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                history.append(LastRunAttemptStarted(at=1))

            persisted = json.loads(path.read_text())
            entries = history.entries

        self.assertEqual([], entries[:-1])
        self.assertEqual([{"at": 1.0, "kind": "started"}], persisted)
        log_messages = [r.message for r in logs.records]
        joined_log_messages = "\n".join(log_messages)
        self.assertIn("Ignoring invalid history file", joined_log_messages)
        self.assertIn(
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            joined_log_messages,
        )

    def test_unexpected_top_level_json_logs_and_starts_clean(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text("42")

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                entries = history.entries

        self.assertEqual([], entries)
        log_messages = [r.message for r in logs.records]
        joined_log_messages = "\n".join(log_messages)
        self.assertIn("Ignoring invalid history file", joined_log_messages)
        self.assertIn("expected a list", joined_log_messages)

    def test_invalid_entries_are_logged_and_skipped(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text(
                json.dumps(
                    [
                        {"at": 1, "kind": "started"},
                        {"at": 2, "kind": "finished", "outcome": "weird"},
                        "not an entry",
                        {"at": 3, "kind": "finished", "outcome": "success"},
                    ]
                )
            )

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                entries = history.entries

        self.assertEqual(2, len(entries))
        self.assertIsInstance(entries[0], LastRunAttemptStarted)
        entry1 = entries[1]
        self.assertIsInstance(entry1, LastRunAttemptFinished)
        assert isinstance(entry1, LastRunAttemptFinished)
        self.assertEqual("success", entry1.outcome)
        self.assertTrue(
            any(
                "Input should be 'success', 'failure' or 'neutral' [type=literal_error, input_value='weird', input_type=str]"
                in r.message
                for r in logs.records
            ),
        )
        self.assertTrue(
            any(
                "Input should be a valid dictionary or object to extract fields from [type=model_attributes_type, input_value='not an entry', input_type=str]"
                in r.message
                for r in logs.records
            ),
        )

    def test_invalid_wrapped_entries_logs_and_starts_clean(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text(json.dumps({"entries": {"at": 1}}))

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                entries = history.entries

        self.assertEqual([], entries)
        log_messages = [r.message for r in logs.records]
        joined_log_messages = "\n".join(log_messages)
        self.assertIn("Ignoring invalid history file", joined_log_messages)
        self.assertIn("expected a list", joined_log_messages)

    def test_unrecognized_object_logs_and_starts_clean(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text(json.dumps({"not": "history"}))

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                entries = history.entries

        self.assertEqual([], entries)
        log_messages = [r.message for r in logs.records]
        joined_log_messages = "\n".join(log_messages)
        self.assertIn("Ignoring invalid history file", joined_log_messages)
        self.assertIn("expected a list", joined_log_messages)

    def test_old_summary_state_logs_and_starts_clean(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            path = Path(tmp_dir_str) / "last-run.json"
            path.write_text(
                json.dumps(
                    {
                        "last_attempted": "not a datetime",
                        "last_successful": "2025-01-01T10:00:00",
                        "last_failed": "2025-01-01T12:00:00",
                        "n_consecutive_failures": "not an int",
                        "last_failure": "boom",
                    }
                )
            )

            with self.assertLogs(LOGGER_NAME, level="ERROR") as logs:
                history = LastRunHistory(path=path)
                entries = history.entries

        self.assertEqual([], entries)
        log_messages = [r.message for r in logs.records]
        joined_log_messages = "\n".join(log_messages)
        self.assertIn("Ignoring invalid history file", joined_log_messages)
        self.assertIn("expected a list", joined_log_messages)


if __name__ == "__main__":
    unittest.main()
