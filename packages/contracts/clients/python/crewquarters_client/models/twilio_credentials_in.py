from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="TwilioCredentialsIn")


@_attrs_define
class TwilioCredentialsIn:
    """
    Attributes:
        account_sid (str):
        auth_token (str): Stored; never returned.
        from_number (str):
    """

    account_sid: str
    auth_token: str
    from_number: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_sid = self.account_sid

        auth_token = self.auth_token

        from_number = self.from_number

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "accountSid": account_sid,
                "authToken": auth_token,
                "fromNumber": from_number,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_sid = d.pop("accountSid")

        auth_token = d.pop("authToken")

        from_number = d.pop("fromNumber")

        twilio_credentials_in = cls(
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
        )

        twilio_credentials_in.additional_properties = d
        return twilio_credentials_in

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
