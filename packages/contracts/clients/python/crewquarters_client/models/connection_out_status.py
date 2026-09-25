from enum import StrEnum


class ConnectionOutStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DISABLED = "DISABLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    NOT_CONNECTED = "NOT_CONNECTED"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return str(self.value)
