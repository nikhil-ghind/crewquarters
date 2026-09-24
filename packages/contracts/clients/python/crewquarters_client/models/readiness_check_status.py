from enum import StrEnum


class ReadinessCheckStatus(StrEnum):
    MISSING = "missing"
    NEEDS_ATTENTION = "needs_attention"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
