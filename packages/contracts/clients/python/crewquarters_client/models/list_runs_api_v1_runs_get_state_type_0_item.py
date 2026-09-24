from enum import StrEnum


class ListRunsApiV1RunsGetStateType0Item(StrEnum):
    CANCELLED = "CANCELLED"
    CANCELLING = "CANCELLING"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    LOADING_MODEL = "LOADING_MODEL"
    PREPARING = "PREPARING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING_INPUT = "WAITING_INPUT"

    def __str__(self) -> str:
        return str(self.value)
