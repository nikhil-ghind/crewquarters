from enum import StrEnum


class RunOutTrigger(StrEnum):
    AGENT = "agent"
    MANUAL = "manual"
    SCHEDULE = "schedule"

    def __str__(self) -> str:
        return str(self.value)
