from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.heartbeat_out_state import HeartbeatOutState

T = TypeVar("T", bound="HeartbeatOut")


@_attrs_define
class HeartbeatOut:
    """
    Attributes:
        state (HeartbeatOutState):
        cancel_requested (bool):
    """

    state: HeartbeatOutState
    cancel_requested: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        cancel_requested = self.cancel_requested

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
                "cancelRequested": cancel_requested,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        state = HeartbeatOutState(d.pop("state"))

        cancel_requested = d.pop("cancelRequested")

        heartbeat_out = cls(
            state=state,
            cancel_requested=cancel_requested,
        )

        heartbeat_out.additional_properties = d
        return heartbeat_out

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
