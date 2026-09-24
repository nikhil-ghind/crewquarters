from enum import StrEnum


class RunOutTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"

    def __str__(self) -> str:
        return str(self.value)
