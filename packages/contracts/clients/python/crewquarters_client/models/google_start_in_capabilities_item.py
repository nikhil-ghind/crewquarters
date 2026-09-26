from enum import StrEnum


class GoogleStartInCapabilitiesItem(StrEnum):
    GMAIL_READONLY = "gmail.readonly"
    GMAIL_SEND = "gmail.send"
    SPREADSHEETS = "spreadsheets"

    def __str__(self) -> str:
        return str(self.value)
