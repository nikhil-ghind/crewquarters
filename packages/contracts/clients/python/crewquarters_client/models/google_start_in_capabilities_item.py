from enum import StrEnum


class GoogleStartInCapabilitiesItem(StrEnum):
    GMAIL_READONLY = "gmail.readonly"
    SPREADSHEETS = "spreadsheets"

    def __str__(self) -> str:
        return str(self.value)
