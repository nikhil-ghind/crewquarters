from enum import StrEnum


class BackupOutSource(StrEnum):
    API = "api"
    DEVICE = "device"

    def __str__(self) -> str:
        return str(self.value)
