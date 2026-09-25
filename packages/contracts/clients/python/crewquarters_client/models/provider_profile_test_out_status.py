from enum import StrEnum


class ProviderProfileTestOutStatus(StrEnum):
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return str(self.value)
