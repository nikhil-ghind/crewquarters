from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_profile_test_out_status import ProviderProfileTestOutStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ProviderProfileTestOut")


@_attrs_define
class ProviderProfileTestOut:
    """
    Attributes:
        status (ProviderProfileTestOutStatus):
        checked_at (datetime.datetime):
        detail (None | str | Unset):
    """

    status: ProviderProfileTestOutStatus
    checked_at: datetime.datetime
    detail: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        checked_at = self.checked_at.isoformat()

        detail: str | Unset | None
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
                "checkedAt": checked_at,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        status = ProviderProfileTestOutStatus(d.pop("status"))

        checked_at = datetime.datetime.fromisoformat(d.pop("checkedAt"))

        def _parse_detail(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        provider_profile_test_out = cls(
            status=status,
            checked_at=checked_at,
            detail=detail,
        )

        provider_profile_test_out.additional_properties = d
        return provider_profile_test_out

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
