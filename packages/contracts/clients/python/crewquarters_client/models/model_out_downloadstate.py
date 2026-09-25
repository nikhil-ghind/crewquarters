from enum import StrEnum


class ModelOutDownloadstate(StrEnum):
    DELETING = "DELETING"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    INSTALLED = "INSTALLED"
    NOT_INSTALLED = "NOT_INSTALLED"

    def __str__(self) -> str:
        return str(self.value)
