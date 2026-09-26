from enum import StrEnum


class VoiceTurnOutRole(StrEnum):
    ASSISTANT = "assistant"
    CALLER = "caller"

    def __str__(self) -> str:
        return str(self.value)
