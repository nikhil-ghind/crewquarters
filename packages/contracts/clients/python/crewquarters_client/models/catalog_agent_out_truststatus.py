from enum import StrEnum


class CatalogAgentOutTruststatus(StrEnum):
    CURATED = "curated"
    IMPORTED_UNREVIEWED = "imported_unreviewed"

    def __str__(self) -> str:
        return str(self.value)
