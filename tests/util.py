import re
from datetime import datetime, date
from re import Pattern
from typing import Any, Self

from comparable_pattern import ComparablePattern


class AnyDateTime(datetime):
    def __new__(cls) -> Self:
        return super().__new__(cls, 1970, 1, 1, 0, 0, 0)

    def __eq__(self, __value: Any) -> bool:
        return isinstance(__value, datetime)


class AnyDate(date):
    def __new__(cls) -> Self:
        return super().__new__(cls, 1970, 1, 1)

    def __eq__(self, __value: Any) -> bool:
        return isinstance(__value, date)


class MatchingException(BaseException):
    """
    Examples:
        Standard behavior:
        >>> RuntimeError("e") == RuntimeError("e")
        False

        Our custom behavior:
        >>> MatchingException(match_message="e", match_type=RuntimeError) == RuntimeError("e")
        True
    """

    def __init__(
        self,
        match_message: str | Pattern[str] | None,
        match_type: type[BaseException] | None,
    ) -> None:
        super().__init__()
        self.match_message = match_message
        self.match_type = match_type

    def __eq__(self, __value: Any) -> bool:
        if not isinstance(__value, BaseException):
            return False

        if self.match_type is not None and not isinstance(__value, self.match_type):
            return False

        if self.match_message is not None:
            message = str(__value)
            if isinstance(self.match_message, str):
                return message == self.match_message
            else:
                return self.match_message.search(message) is not None

        return True


any_uuid = ComparablePattern(re.compile(r"[\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12}"))
