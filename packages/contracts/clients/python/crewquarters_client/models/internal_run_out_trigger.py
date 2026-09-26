from enum import StrEnum


class InternalRunOutTrigger(StrEnum):
    AGENT = "agent"
    MANUAL = "manual"
    SCHEDULE = "schedule"

    def __str__(self) -> str:
        return str(self.value)
