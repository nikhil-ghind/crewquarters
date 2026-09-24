from enum import StrEnum


class StatusCheckStatus(StrEnum):
    FAILED = "failed"
    PASSED = "passed"
    WARNING = "warning"

    def __str__(self) -> str:
        return str(self.value)
