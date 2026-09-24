from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.schedule_patch_in_misfire_policy_type_0 import SchedulePatchInMisfirePolicyType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="SchedulePatchIn")


@_attrs_define
class SchedulePatchIn:
    """
    Attributes:
        version (int):
        cron (None | str | Unset):
        timezone (None | str | Unset):
        misfire_policy (None | SchedulePatchInMisfirePolicyType0 | Unset):
        enabled (bool | None | Unset):
    """

    version: int
    cron: str | Unset | None = UNSET
    timezone: str | Unset | None = UNSET
    misfire_policy: SchedulePatchInMisfirePolicyType0 | Unset | None = UNSET
    enabled: bool | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        cron: str | Unset | None
        if isinstance(self.cron, Unset):
            cron = UNSET
        else:
            cron = self.cron

        timezone: str | Unset | None
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        misfire_policy: str | Unset | None
        if isinstance(self.misfire_policy, Unset):
            misfire_policy = UNSET
        elif isinstance(self.misfire_policy, SchedulePatchInMisfirePolicyType0):
            misfire_policy = self.misfire_policy.value
        else:
            misfire_policy = self.misfire_policy

        enabled: bool | Unset | None
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "version": version,
            }
        )
        if cron is not UNSET:
            field_dict["cron"] = cron
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if misfire_policy is not UNSET:
            field_dict["misfirePolicy"] = misfire_policy
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        version = d.pop("version")

        def _parse_cron(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cron = _parse_cron(d.pop("cron", UNSET))

        def _parse_timezone(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        def _parse_misfire_policy(data: object) -> SchedulePatchInMisfirePolicyType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                misfire_policy_type_0 = SchedulePatchInMisfirePolicyType0(data)

                return misfire_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SchedulePatchInMisfirePolicyType0 | Unset, data)

        misfire_policy = _parse_misfire_policy(d.pop("misfirePolicy", UNSET))

        def _parse_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        schedule_patch_in = cls(
            version=version,
            cron=cron,
            timezone=timezone,
            misfire_policy=misfire_policy,
            enabled=enabled,
        )

        schedule_patch_in.additional_properties = d
        return schedule_patch_in

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
