from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.connection_out_provider import ConnectionOutProvider
from ..models.connection_out_status import ConnectionOutStatus

T = TypeVar("T", bound="ConnectionOut")


@_attrs_define
class ConnectionOut:
    """
    Attributes:
        provider (ConnectionOutProvider):
        display_name (str):
        status (ConnectionOutStatus):
        granted_capabilities (list[str]):
        last_checked_at (datetime.datetime | None):
    """

    provider: ConnectionOutProvider
    display_name: str
    status: ConnectionOutStatus
    granted_capabilities: list[str]
    last_checked_at: datetime.datetime | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        provider = self.provider.value

        display_name = self.display_name

        status = self.status.value

        granted_capabilities = self.granted_capabilities

        last_checked_at: str | None
        if isinstance(self.last_checked_at, datetime.datetime):
            last_checked_at = self.last_checked_at.isoformat()
        else:
            last_checked_at = self.last_checked_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "provider": provider,
                "displayName": display_name,
                "status": status,
                "grantedCapabilities": granted_capabilities,
                "lastCheckedAt": last_checked_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider = ConnectionOutProvider(d.pop("provider"))

        display_name = d.pop("displayName")

        status = ConnectionOutStatus(d.pop("status"))

        granted_capabilities = cast(list[str], d.pop("grantedCapabilities"))

        def _parse_last_checked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_checked_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_checked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_checked_at = _parse_last_checked_at(d.pop("lastCheckedAt"))

        connection_out = cls(
            provider=provider,
            display_name=display_name,
            status=status,
            granted_capabilities=granted_capabilities,
            last_checked_at=last_checked_at,
        )

        connection_out.additional_properties = d
        return connection_out

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
