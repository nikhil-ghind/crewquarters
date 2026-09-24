from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.readiness_check_name import ReadinessCheckName
from ..models.readiness_check_status import ReadinessCheckStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ReadinessCheck")


@_attrs_define
class ReadinessCheck:
    """
    Attributes:
        name (ReadinessCheckName):
        status (ReadinessCheckStatus):
        detail (str):
        resource (None | str | Unset):
    """

    name: ReadinessCheckName
    status: ReadinessCheckStatus
    detail: str
    resource: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name.value

        status = self.status.value

        detail = self.detail

        resource: str | Unset | None
        if isinstance(self.resource, Unset):
            resource = UNSET
        else:
            resource = self.resource

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "status": status,
                "detail": detail,
            }
        )
        if resource is not UNSET:
            field_dict["resource"] = resource

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = ReadinessCheckName(d.pop("name"))

        status = ReadinessCheckStatus(d.pop("status"))

        detail = d.pop("detail")

        def _parse_resource(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        resource = _parse_resource(d.pop("resource", UNSET))

        readiness_check = cls(
            name=name,
            status=status,
            detail=detail,
            resource=resource,
        )

        readiness_check.additional_properties = d
        return readiness_check

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
