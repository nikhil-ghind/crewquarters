from enum import StrEnum


class ConnectionOutProvider(StrEnum):
    ANTHROPIC = "anthropic"
    GITHUB = "github"
    GOOGLE = "google"
    OPENAI = "openai"
    TWILIO = "twilio"

    def __str__(self) -> str:
        return str(self.value)
