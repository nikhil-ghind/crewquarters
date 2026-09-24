from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.system_status_out_status import SystemStatusOutStatus

if TYPE_CHECKING:
    from ..models.status_check import StatusCheck
    from ..models.system_status_out_runtime import SystemStatusOutRuntime


T = TypeVar("T", bound="SystemStatusOut")


@_attrs_define
class SystemStatusOut:
    """
    Attributes:
        status (SystemStatusOutStatus):
        profile (str):
        version (str):
        architecture (str):
        checks (list[StatusCheck]):
        runtime (SystemStatusOutRuntime):
    """

    status: SystemStatusOutStatus
    profile: str
    version: str
    architecture: str
    checks: list[StatusCheck]
    runtime: SystemStatusOutRuntime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        profile = self.profile

        version = self.version

        architecture = self.architecture

        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        runtime = self.runtime.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
                "profile": profile,
                "version": version,
                "architecture": architecture,
                "checks": checks,
                "runtime": runtime,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.status_check import StatusCheck
        from ..models.system_status_out_runtime import SystemStatusOutRuntime

        d = dict(src_dict)
        status = SystemStatusOutStatus(d.pop("status"))

        profile = d.pop("profile")

        version = d.pop("version")

        architecture = d.pop("architecture")

        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = StatusCheck.from_dict(checks_item_data)

            checks.append(checks_item)

        runtime = SystemStatusOutRuntime.from_dict(d.pop("runtime"))

        system_status_out = cls(
            status=status,
            profile=profile,
            version=version,
            architecture=architecture,
            checks=checks,
            runtime=runtime,
        )

        system_status_out.additional_properties = d
        return system_status_out

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
