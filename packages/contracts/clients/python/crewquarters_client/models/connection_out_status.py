from enum import StrEnum


class ConnectionOutStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DISABLED = "DISABLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    NOT_CONNECTED = "NOT_CONNECTED"

    def __str__(self) -> str:
        return str(self.value)
