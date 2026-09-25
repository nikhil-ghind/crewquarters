from enum import StrEnum


class ProviderProfileCreateInProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

    def __str__(self) -> str:
        return str(self.value)
