from enum import StrEnum


class CatalogAgentOutSource(StrEnum):
    BUNDLED = "bundled"
    IMPORTED = "imported"

    def __str__(self) -> str:
        return str(self.value)
