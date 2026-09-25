from enum import StrEnum


class ModelOutMemorystate(StrEnum):
    DRAINING = "DRAINING"
    ERROR = "ERROR"
    LOADING = "LOADING"
    LOAD_ERROR = "LOAD_ERROR"
    NOT_LOADED = "NOT_LOADED"
    READY = "READY"

    def __str__(self) -> str:
        return str(self.value)
