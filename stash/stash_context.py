from datetime import date, datetime, timezone

from requests.structures import CaseInsensitiveDict
from stashapi.stashapp import StashInterface


class StashContext:
    def __init__(self, conn: dict):
        self.conn = CaseInsensitiveDict(conn)
        self.interface = StashInterface(conn=self.conn)

    def get_interface(self) -> StashInterface:
        return self.interface


class StashQL:
    @staticmethod
    def sanitize_datetime(value: str | date | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).astimezone(timezone.utc)
            except ValueError:
                raise ValueError(f"Invalid date string: {value}")
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        raise TypeError(f"Unsupported type: {type(value)}")
