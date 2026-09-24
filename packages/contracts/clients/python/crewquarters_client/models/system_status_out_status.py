from enum import StrEnum


class SystemStatusOutStatus(StrEnum):
    DEGRADED = "degraded"
    HEALTHY = "healthy"
    OFFLINE = "offline"

    def __str__(self) -> str:
        return str(self.value)
