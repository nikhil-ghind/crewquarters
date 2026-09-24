from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OccurrenceOut")


@_attrs_define
class OccurrenceOut:
    """
    Attributes:
        at (datetime.datetime): Occurrence instant in UTC.
        local (str): Local wall-clock time with UTC offset, ISO 8601.
        zone_abbreviation (str):
    """

    at: datetime.datetime
    local: str
    zone_abbreviation: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        at = self.at.isoformat()

        local = self.local

        zone_abbreviation = self.zone_abbreviation

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "at": at,
                "local": local,
                "zoneAbbreviation": zone_abbreviation,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        at = datetime.datetime.fromisoformat(d.pop("at"))

        local = d.pop("local")

        zone_abbreviation = d.pop("zoneAbbreviation")

        occurrence_out = cls(
            at=at,
            local=local,
            zone_abbreviation=zone_abbreviation,
        )

        occurrence_out.additional_properties = d
        return occurrence_out

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
