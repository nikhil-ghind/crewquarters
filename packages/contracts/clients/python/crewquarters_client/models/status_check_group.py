from enum import StrEnum


class StatusCheckGroup(StrEnum):
    DATABASE = "database"
    DEVICE = "device"
    MODELS = "models"
    NETWORK = "network"
    RUNTIME = "runtime"
    STORAGE = "storage"

    def __str__(self) -> str:
        return str(self.value)
