from enum import StrEnum


class ConnectionOutProvider(StrEnum):
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI = "openai"
    TWILIO = "twilio"

    def __str__(self) -> str:
        return str(self.value)
