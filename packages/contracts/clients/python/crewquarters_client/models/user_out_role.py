from enum import StrEnum


class UserOutRole(StrEnum):
    MEMBER = "member"
    OWNER = "owner"

    def __str__(self) -> str:
        return str(self.value)
