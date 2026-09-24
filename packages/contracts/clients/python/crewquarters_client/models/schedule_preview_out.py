from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.occurrence_out import OccurrenceOut


T = TypeVar("T", bound="SchedulePreviewOut")


@_attrs_define
class SchedulePreviewOut:
    """
    Attributes:
        cron (str):
        timezone (str):
        occurrences (list[OccurrenceOut]):
    """

    cron: str
    timezone: str
    occurrences: list[OccurrenceOut]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        timezone = self.timezone

        occurrences = []
        for occurrences_item_data in self.occurrences:
            occurrences_item = occurrences_item_data.to_dict()
            occurrences.append(occurrences_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cron": cron,
                "timezone": timezone,
                "occurrences": occurrences,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.occurrence_out import OccurrenceOut

        d = dict(src_dict)
        cron = d.pop("cron")

        timezone = d.pop("timezone")

        occurrences = []
        _occurrences = d.pop("occurrences")
        for occurrences_item_data in _occurrences:
            occurrences_item = OccurrenceOut.from_dict(occurrences_item_data)

            occurrences.append(occurrences_item)

        schedule_preview_out = cls(
            cron=cron,
            timezone=timezone,
            occurrences=occurrences,
        )

        schedule_preview_out.additional_properties = d
        return schedule_preview_out

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
