from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.batch_event import BatchEvent


T = TypeVar("T", bound="EventBatchIn")


@_attrs_define
class EventBatchIn:
    """
    Attributes:
        attempt (int):
        events (list[BatchEvent]):
    """

    attempt: int
    events: list[BatchEvent]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attempt = self.attempt

        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempt": attempt,
                "events": events,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.batch_event import BatchEvent

        d = dict(src_dict)
        attempt = d.pop("attempt")

        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = BatchEvent.from_dict(events_item_data)

            events.append(events_item)

        event_batch_in = cls(
            attempt=attempt,
            events=events,
        )

        event_batch_in.additional_properties = d
        return event_batch_in

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
