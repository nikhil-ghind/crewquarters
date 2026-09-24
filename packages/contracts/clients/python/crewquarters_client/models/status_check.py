from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.status_check_group import StatusCheckGroup
from ..models.status_check_status import StatusCheckStatus

T = TypeVar("T", bound="StatusCheck")


@_attrs_define
class StatusCheck:
    """
    Attributes:
        group (StatusCheckGroup):
        name (str):
        status (StatusCheckStatus):
        detail (str):
        checked_at (datetime.datetime):
    """

    group: StatusCheckGroup
    name: str
    status: StatusCheckStatus
    detail: str
    checked_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        group = self.group.value

        name = self.name

        status = self.status.value

        detail = self.detail

        checked_at = self.checked_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "group": group,
                "name": name,
                "status": status,
                "detail": detail,
                "checkedAt": checked_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        group = StatusCheckGroup(d.pop("group"))

        name = d.pop("name")

        status = StatusCheckStatus(d.pop("status"))

        detail = d.pop("detail")

        checked_at = datetime.datetime.fromisoformat(d.pop("checkedAt"))

        status_check = cls(
            group=group,
            name=name,
            status=status,
            detail=detail,
            checked_at=checked_at,
        )

        status_check.additional_properties = d
        return status_check

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
