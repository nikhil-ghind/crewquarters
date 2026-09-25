from enum import StrEnum


class ModelLeaseOutHoldertype(StrEnum):
    CHAT = "chat"
    MANUAL = "manual"
    RUN = "run"

    def __str__(self) -> str:
        return str(self.value)
