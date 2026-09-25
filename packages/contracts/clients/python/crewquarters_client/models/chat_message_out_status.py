from enum import StrEnum


class ChatMessageOutStatus(StrEnum):
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"
    STREAMING = "streaming"

    def __str__(self) -> str:
        return str(self.value)
