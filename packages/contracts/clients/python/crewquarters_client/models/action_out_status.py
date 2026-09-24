from enum import StrEnum


class ActionOutStatus(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    IN_DOUBT = "in_doubt"

    def __str__(self) -> str:
        return str(self.value)
