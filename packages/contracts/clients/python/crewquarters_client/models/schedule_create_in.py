from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.schedule_create_in_misfirepolicy import ScheduleCreateInMisfirepolicy
from ..types import UNSET, Unset

T = TypeVar("T", bound="ScheduleCreateIn")


@_attrs_define
class ScheduleCreateIn:
    """
    Attributes:
        installation_id (UUID):
        cron (str):
        timezone (str): IANA timezone name.
        misfire_policy (ScheduleCreateInMisfirepolicy | Unset):  Default: ScheduleCreateInMisfirepolicy.FIRE_ONCE.
        enabled (bool | Unset):  Default: True.
    """

    installation_id: UUID
    cron: str
    timezone: str
    misfire_policy: ScheduleCreateInMisfirepolicy | Unset = ScheduleCreateInMisfirepolicy.FIRE_ONCE
    enabled: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        installation_id = str(self.installation_id)

        cron = self.cron

        timezone = self.timezone

        misfire_policy: str | Unset = UNSET
        if not isinstance(self.misfire_policy, Unset):
            misfire_policy = self.misfire_policy.value

        enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "installationId": installation_id,
                "cron": cron,
                "timezone": timezone,
            }
        )
        if misfire_policy is not UNSET:
            field_dict["misfirePolicy"] = misfire_policy
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        installation_id = UUID(d.pop("installationId"))

        cron = d.pop("cron")

        timezone = d.pop("timezone")

        _misfire_policy = d.pop("misfirePolicy", UNSET)
        misfire_policy: ScheduleCreateInMisfirepolicy | Unset
        if isinstance(_misfire_policy, Unset):
            misfire_policy = UNSET
        else:
            misfire_policy = ScheduleCreateInMisfirepolicy(_misfire_policy)

        enabled = d.pop("enabled", UNSET)

        schedule_create_in = cls(
            installation_id=installation_id,
            cron=cron,
            timezone=timezone,
            misfire_policy=misfire_policy,
            enabled=enabled,
        )

        schedule_create_in.additional_properties = d
        return schedule_create_in

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
