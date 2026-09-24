from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.user_out_role import UserOutRole

T = TypeVar("T", bound="UserOut")


@_attrs_define
class UserOut:
    """
    Attributes:
        id (UUID):
        username (str):
        email (None | str):
        role (UserOutRole):
        created_at (datetime.datetime):
    """

    id: UUID
    username: str
    email: str | None
    role: UserOutRole
    created_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        username = self.username

        email: str | None
        email = self.email

        role = self.role.value

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "username": username,
                "email": email,
                "role": role,
                "createdAt": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        username = d.pop("username")

        def _parse_email(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        email = _parse_email(d.pop("email"))

        role = UserOutRole(d.pop("role"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        user_out = cls(
            id=id,
            username=username,
            email=email,
            role=role,
            created_at=created_at,
        )

        user_out.additional_properties = d
        return user_out

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
