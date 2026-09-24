from enum import StrEnum


class ScheduleCreateInMisfirepolicy(StrEnum):
    FIRE_ONCE = "fire_once"
    SKIP = "skip"

    def __str__(self) -> str:
        return str(self.value)
