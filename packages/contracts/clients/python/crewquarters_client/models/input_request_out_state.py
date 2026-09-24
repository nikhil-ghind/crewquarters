from enum import StrEnum


class InputRequestOutState(StrEnum):
    ANSWERED = "answered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING = "pending"

    def __str__(self) -> str:
        return str(self.value)
