from enum import StrEnum


class ChatSessionCreateInRetrievalmode(StrEnum):
    ONLY_KNOWLEDGE = "only_knowledge"
    WHEN_RELEVANT = "when_relevant"

    def __str__(self) -> str:
        return str(self.value)
