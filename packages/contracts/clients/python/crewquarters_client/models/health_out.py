from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.health_out_status import HealthOutStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.health_out_checks import HealthOutChecks


T = TypeVar("T", bound="HealthOut")


@_attrs_define
class HealthOut:
    """
    Attributes:
        status (HealthOutStatus):
        checks (HealthOutChecks | Unset):
    """

    status: HealthOutStatus
    checks: HealthOutChecks | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        checks: dict[str, Any] | Unset = UNSET
        if not isinstance(self.checks, Unset):
            checks = self.checks.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if checks is not UNSET:
            field_dict["checks"] = checks

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.health_out_checks import HealthOutChecks

        d = dict(src_dict)
        status = HealthOutStatus(d.pop("status"))

        _checks = d.pop("checks", UNSET)
        checks: HealthOutChecks | Unset
        if isinstance(_checks, Unset):
            checks = UNSET
        else:
            checks = HealthOutChecks.from_dict(_checks)

        health_out = cls(
            status=status,
            checks=checks,
        )

        health_out.additional_properties = d
        return health_out

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
