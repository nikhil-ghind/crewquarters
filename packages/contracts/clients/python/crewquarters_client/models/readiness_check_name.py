from enum import StrEnum


class ReadinessCheckName(StrEnum):
    ARCHITECTURE = "architecture"
    CONFIGURATION = "configuration"
    CONNECTION = "connection"
    ENABLED = "enabled"
    MODEL = "model"
    PERMISSIONS = "permissions"

    def __str__(self) -> str:
        return str(self.value)
