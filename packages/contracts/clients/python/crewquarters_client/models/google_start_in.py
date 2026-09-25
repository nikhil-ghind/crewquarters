from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.google_start_in_capabilities_item import GoogleStartInCapabilitiesItem

T = TypeVar("T", bound="GoogleStartIn")


@_attrs_define
class GoogleStartIn:
    """
    Attributes:
        capabilities (list[GoogleStartInCapabilitiesItem]): Consent is requested separately per capability.
    """

    capabilities: list[GoogleStartInCapabilitiesItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        capabilities = []
        for capabilities_item_data in self.capabilities:
            capabilities_item = capabilities_item_data.value
            capabilities.append(capabilities_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "capabilities": capabilities,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        capabilities = []
        _capabilities = d.pop("capabilities")
        for capabilities_item_data in _capabilities:
            capabilities_item = GoogleStartInCapabilitiesItem(capabilities_item_data)

            capabilities.append(capabilities_item)

        google_start_in = cls(
            capabilities=capabilities,
        )

        google_start_in.additional_properties = d
        return google_start_in

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
