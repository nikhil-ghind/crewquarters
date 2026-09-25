from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ActionClaimIn")


@_attrs_define
class ActionClaimIn:
    """
    Attributes:
        attempt (int):
        claim_token (None | str | Unset): Random per claim() call, reused on its retries. A repeat with the same token
            from the same attempt returns the original result.
    """

    attempt: int
    claim_token: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attempt = self.attempt

        claim_token: str | Unset | None
        if isinstance(self.claim_token, Unset):
            claim_token = UNSET
        else:
            claim_token = self.claim_token

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempt": attempt,
            }
        )
        if claim_token is not UNSET:
            field_dict["claimToken"] = claim_token

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        attempt = d.pop("attempt")

        def _parse_claim_token(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        claim_token = _parse_claim_token(d.pop("claimToken", UNSET))

        action_claim_in = cls(
            attempt=attempt,
            claim_token=claim_token,
        )

        action_claim_in.additional_properties = d
        return action_claim_in

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
