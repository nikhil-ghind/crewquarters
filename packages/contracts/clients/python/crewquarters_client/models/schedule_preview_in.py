from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SchedulePreviewIn")


@_attrs_define
class SchedulePreviewIn:
    """
    Attributes:
        cron (str):
        timezone (str):
        count (int | Unset):  Default: 3.
    """

    cron: str
    timezone: str
    count: int | Unset = 3
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        timezone = self.timezone

        count = self.count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cron": cron,
                "timezone": timezone,
            }
        )
        if count is not UNSET:
            field_dict["count"] = count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cron = d.pop("cron")

        timezone = d.pop("timezone")

        count = d.pop("count", UNSET)

        schedule_preview_in = cls(
            cron=cron,
            timezone=timezone,
            count=count,
        )

        schedule_preview_in.additional_properties = d
        return schedule_preview_in

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
