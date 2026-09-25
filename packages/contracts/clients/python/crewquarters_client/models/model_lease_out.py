from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_lease_out_holdertype import ModelLeaseOutHoldertype

T = TypeVar("T", bound="ModelLeaseOut")


@_attrs_define
class ModelLeaseOut:
    """
    Attributes:
        id (str):
        holder_type (ModelLeaseOutHoldertype):
        holder_id (str):
        label (str): Friendly holder name, e.g. 'Chat: Contracts' or 'Run 01a0d4c2'.
        expires_at (datetime.datetime):
    """

    id: str
    holder_type: ModelLeaseOutHoldertype
    holder_id: str
    label: str
    expires_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        holder_type = self.holder_type.value

        holder_id = self.holder_id

        label = self.label

        expires_at = self.expires_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "holderType": holder_type,
                "holderId": holder_id,
                "label": label,
                "expiresAt": expires_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        holder_type = ModelLeaseOutHoldertype(d.pop("holderType"))

        holder_id = d.pop("holderId")

        label = d.pop("label")

        expires_at = datetime.datetime.fromisoformat(d.pop("expiresAt"))

        model_lease_out = cls(
            id=id,
            holder_type=holder_type,
            holder_id=holder_id,
            label=label,
            expires_at=expires_at,
        )

        model_lease_out.additional_properties = d
        return model_lease_out

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
