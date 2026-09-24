from enum import StrEnum


class RunResultInStatus(StrEnum):
    FAILED = "failed"
    SUCCEEDED = "succeeded"

    def __str__(self) -> str:
        return str(self.value)
