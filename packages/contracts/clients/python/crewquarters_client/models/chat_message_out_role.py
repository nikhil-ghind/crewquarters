from enum import StrEnum


class ChatMessageOutRole(StrEnum):
    ASSISTANT = "assistant"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
