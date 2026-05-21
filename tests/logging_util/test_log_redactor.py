import unittest
from logging import DEBUG, getLogger, Handler, LogRecord, Formatter
from typing import List, Tuple, Dict, Any

from scheduling_util import attach_extra_callbacks_to_log_formatter, LogRedactor


class CaptureLogHandler(Handler):

    def __init__(
        self,
        *,
        level: int,
        logs: List[Tuple[Dict[str, Any], str]],
    ):
        super().__init__(level=level)
        self.logs = logs

    def emit(self, record: LogRecord) -> None:
        formatted = self.format(record)
        self.logs.append(
            (
                {
                    "args": record.args,
                    "msg": record.msg,
                    "message": getattr(record, "message", None),
                },
                formatted,
            )
        )


def _set_up_logger_and_redactor() -> (
    Tuple[List[Tuple[Dict[str, Any], str]], LogRedactor]
):
    root_logger = getLogger()

    # Remove handlers from previous tests:
    while len(root_logger.handlers):
        root_logger.removeHandler(root_logger.handlers[0])

    root_logger.setLevel(DEBUG)
    logs: List[Tuple[Dict[str, Any], str]] = []
    handler = CaptureLogHandler(level=DEBUG, logs=logs)
    formatter = Formatter()
    handler.setFormatter(formatter)
    redactor = LogRedactor()
    attach_extra_callbacks_to_log_formatter(
        formatter=formatter,
        pre_format=redactor.process_log_record,
        post_format=redactor.process_log_message,
    )
    root_logger.addHandler(handler)
    return logs, redactor


class TestLogRedactor(unittest.TestCase):

    def test_no_args(self) -> None:
        """
        Redact secrets passed in the log message template.
        """
        logs, redactor = _set_up_logger_and_redactor()

        logger = getLogger("test")
        logger.info("one two three")
        redactor.redactions.append("two")
        logger.info("one two three")

        self.assertEqual(
            [
                (
                    {
                        "args": (),
                        "message": "one two three",
                        "msg": "one two three",
                    },
                    "one two three",
                ),
                (
                    {
                        "args": (),
                        "message": "one *** three",
                        "msg": "one *** three",
                    },
                    "one *** three",
                ),
            ],
            logs,
        )

    def test_args_tuple(self) -> None:
        """
        Redact secrets that are passed as tuple/positional args.
        """
        logs, redactor = _set_up_logger_and_redactor()

        logger = getLogger("test.tuple")
        logger.info("one %s three", "two")
        redactor.redactions.append("two")
        logger.info("one %s three", "two")

        self.assertEqual(
            [
                (
                    {
                        "args": ("two",),
                        "message": "one two three",
                        "msg": "one %s three",
                    },
                    "one two three",
                ),
                (
                    {
                        "args": ("***",),
                        "message": "one *** three",
                        "msg": "one %s three",
                    },
                    "one *** three",
                ),
            ],
            logs,
        )

    def test_args_dict(self) -> None:
        """
        Redact secrets that are passed as mapping args (keyword style).
        """
        logs, redactor = _set_up_logger_and_redactor()

        logger = getLogger("test.dict")
        logger.info("one %(secret)s three", {"secret": "two"})
        redactor.redactions.append("two")
        logger.info("one %(secret)s three", {"secret": "two"})

        self.assertEqual(
            [
                (
                    {
                        "args": {"secret": "two"},
                        "message": "one two three",
                        "msg": "one %(secret)s three",
                    },
                    "one two three",
                ),
                (
                    {
                        "args": {"secret": "***"},
                        "message": "one *** three",
                        "msg": "one %(secret)s three",
                    },
                    "one *** three",
                ),
            ],
            logs,
        )

    def test_post_format(self) -> None:
        """
        Redact secrets that only appear after formatting the log message.
        """
        logs, redactor = _set_up_logger_and_redactor()

        logger = getLogger("test.tuple")
        logger.info("two is company, but %s is a crowd", "three")
        redactor.redactions.append("three is a crowd")
        logger.info("two is company, but %s is a crowd", "three")

        self.assertEqual(
            [
                (
                    {
                        "args": ("three",),
                        "message": "two is company, but three is a crowd",
                        "msg": "two is company, but %s is a crowd",
                    },
                    "two is company, but three is a crowd",
                ),
                (
                    {
                        "args": ("three",),
                        # We don't have an opportunity to redact `message` here:
                        "message": "two is company, but three is a crowd",
                        "msg": "two is company, but %s is a crowd",
                    },
                    "two is company, but ***",
                ),
            ],
            logs,
        )
