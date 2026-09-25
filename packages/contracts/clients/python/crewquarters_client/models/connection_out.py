from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.connection_out_provider import ConnectionOutProvider
from ..models.connection_out_status import ConnectionOutStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectionOut")


@_attrs_define
class ConnectionOut:
    """
    Attributes:
        provider (ConnectionOutProvider):
        display_name (str):
        status (ConnectionOutStatus): UNKNOWN: the capability broker could not be reached; nothing is assumed.
        granted_capabilities (list[str]):
        last_checked_at (datetime.datetime | None):
        account (None | str | Unset): Masked account label, when connected.
        detail (None | str | Unset):
    """

    provider: ConnectionOutProvider
    display_name: str
    status: ConnectionOutStatus
    granted_capabilities: list[str]
    last_checked_at: datetime.datetime | None
    account: str | Unset | None = UNSET
    detail: str | Unset | None = UNSET
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

        account: str | Unset | None
        if isinstance(self.account, Unset):
            account = UNSET
        else:
            account = self.account

        detail: str | Unset | None
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

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
        if account is not UNSET:
            field_dict["account"] = account
        if detail is not UNSET:
            field_dict["detail"] = detail

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

        def _parse_account(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        account = _parse_account(d.pop("account", UNSET))

        def _parse_detail(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        connection_out = cls(
            provider=provider,
            display_name=display_name,
            status=status,
            granted_capabilities=granted_capabilities,
            last_checked_at=last_checked_at,
            account=account,
            detail=detail,
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
