from enum import StrEnum


class DocumentOutState(StrEnum):
    FAILED = "FAILED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"

    def __str__(self) -> str:
        return str(self.value)
