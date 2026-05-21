import json
from dataclasses import dataclass, field
from functools import wraps
from logging import (
    getLogger,
    DEBUG,
    StreamHandler,
    getLevelNamesMapping,
    INFO,
    FileHandler,
    LogRecord,
    Formatter,
    Handler,
)
from pathlib import Path
from sys import stderr
from typing import Dict, Any, Callable, List

from coloredlogs import ColoredFormatter


@dataclass(kw_only=True)
class LogRedactor:
    redactions: List[str] = field(default_factory=list)

    def process_log_record(self, record: LogRecord) -> LogRecord:
        for redaction in self.redactions:
            if isinstance(record.msg, str) and redaction in record.msg:
                record.msg = record.msg.replace(redaction, "***")
            if (
                hasattr(record, "message")
                and isinstance(record.message, str)
                and redaction in record.message
            ):
                record.message = record.message.replace(redaction, "***")
            if isinstance(record.args, dict):
                for key, arg in record.args.items():
                    if isinstance(arg, str) and redaction in arg:
                        record.args[key] = arg.replace(redaction, "***")
            elif isinstance(record.args, tuple):
                new_args = list(record.args)
                args_changed = False
                for i, arg in enumerate(new_args):
                    if isinstance(arg, str) and redaction in arg:
                        new_args[i] = arg.replace(redaction, "***")
                        args_changed = True
                if args_changed:
                    record.args = tuple(new_args)
            elif record.args is None:
                pass
            else:
                raise NotImplementedError(
                    f"Don't know how to redact args of type `{type(record.args)}` yet."
                )
        return record

    def process_log_message(self, message: str) -> str:
        for redaction in self.redactions:
            if redaction in message:
                message = message.replace(redaction, "***")
        return message


def attach_extra_callbacks_to_log_formatter(
    *,
    formatter: Formatter,
    pre_format: Callable[[LogRecord], LogRecord] | None,
    post_format: Callable[[str], str] | None,
) -> Formatter:
    if pre_format is None and post_format is None:
        return formatter

    original_format_function = formatter.format

    @wraps(original_format_function)
    def new_format_function(record: LogRecord) -> str:
        if pre_format:
            record = pre_format(record)
        formatted = original_format_function(record)
        if post_format:
            formatted = post_format(formatted)
        return formatted

    formatter.format = new_format_function  # type: ignore[method-assign]

    return formatter


def stream_logs_to_stderr(
    *,
    log_level: int | str,
    pre_format: Callable[[LogRecord], LogRecord] | None = None,
    post_format: Callable[[str], str] | None = None,
) -> None:
    root_logger = getLogger()
    root_logger.setLevel(DEBUG)

    log_level_number: int = (
        log_level
        if isinstance(log_level, int)
        else getLevelNamesMapping()[log_level.upper()]
    )

    # Cap the log level for noisy loggers.
    caps: Dict[str, int] = {
        "urllib3.connectionpool": INFO,
        "paramiko.transport": INFO,
        "invoke": INFO,
    }
    for name, cap in caps.items():
        getLogger(name).setLevel(max(cap, log_level_number))

    # Stream logs to the terminal.
    handler = StreamHandler(stream=stderr)
    handler.setLevel(log_level)
    formatter = ColoredFormatter(fmt="%(name)s %(levelname)s: %(message)s")
    attach_extra_callbacks_to_log_formatter(
        formatter=formatter,
        pre_format=pre_format,
        post_format=post_format,
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def stream_logs_to_jsonl_file(
    *,
    log_file_path: Path,
    log_level: int | str,
    pre_format: Callable[[LogRecord], LogRecord] | None = None,
    post_format: Callable[[str], str] | None = None,
) -> None:
    root_logger = getLogger()
    root_logger.setLevel(DEBUG)

    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    handler = FileHandler(
        filename=log_file_path,
        mode="w",
        encoding="utf-8",
        errors="replace",
    )
    handler.setLevel(log_level)
    formatter = JsonLogFormatter()
    attach_extra_callbacks_to_log_formatter(
        formatter=formatter,
        pre_format=pre_format,
        post_format=post_format,
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


class JsonLogFormatter(Formatter):
    """
    Format log records as JSON lines. See https://jsonlines.org/ .
    """

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: LogRecord) -> str:
        return json.dumps(
            obj={
                **record.__dict__,
                "msg": record.getMessage(),
            },
            cls=JsonLogEncoder,
            allow_nan=False,
        )


class JsonLogEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        try:
            return super().default(o)
        except TypeError:
            try:
                return repr(o)
            except BaseException as e:
                return f"Failed to serialize object: {e}"


class ProcessIdSpyHandler(Handler):
    """
    A logging handler that looks for lines of the form `PID=12345` in log messages, and stores the PID in the `pid` attribute.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pid: int | None = None

    def emit(self, record: LogRecord) -> None:
        msg = self.format(record)
        prefix = "PID="
        if msg.startswith(prefix):
            try:
                self.pid = int(msg[len(prefix) :])
            except ValueError:
                pass
