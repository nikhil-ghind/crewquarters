from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_event_in_type import AgentEventInType

if TYPE_CHECKING:
    from ..models.agent_event_in_payload import AgentEventInPayload


T = TypeVar("T", bound="AgentEventIn")


@_attrs_define
class AgentEventIn:
    """
    Attributes:
        attempt (int):
        type_ (AgentEventInType):
        payload (AgentEventInPayload):
    """

    attempt: int
    type_: AgentEventInType
    payload: AgentEventInPayload
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attempt = self.attempt

        type_ = self.type_.value

        payload = self.payload.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempt": attempt,
                "type": type_,
                "payload": payload,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_event_in_payload import AgentEventInPayload

        d = dict(src_dict)
        attempt = d.pop("attempt")

        type_ = AgentEventInType(d.pop("type"))

        payload = AgentEventInPayload.from_dict(d.pop("payload"))

        agent_event_in = cls(
            attempt=attempt,
            type_=type_,
            payload=payload,
        )

        agent_event_in.additional_properties = d
        return agent_event_in

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
