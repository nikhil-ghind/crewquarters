from enum import StrEnum


class ScheduleOutMisfirepolicy(StrEnum):
    FIRE_ONCE = "fire_once"
    SKIP = "skip"

    def __str__(self) -> str:
        return str(self.value)
