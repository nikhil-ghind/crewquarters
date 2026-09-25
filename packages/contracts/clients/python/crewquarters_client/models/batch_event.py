from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.batch_event_payload import BatchEventPayload


T = TypeVar("T", bound="BatchEvent")


@_attrs_define
class BatchEvent:
    """
    Attributes:
        client_event_id (str):
        type_ (str): run.log, run.progress, run.metric, or run.artifact.
        payload (BatchEventPayload):
        occurred_at (datetime.datetime | None | Unset):
    """

    client_event_id: str
    type_: str
    payload: BatchEventPayload
    occurred_at: datetime.datetime | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        client_event_id = self.client_event_id

        type_ = self.type_

        payload = self.payload.to_dict()

        occurred_at: str | Unset | None
        if isinstance(self.occurred_at, Unset):
            occurred_at = UNSET
        elif isinstance(self.occurred_at, datetime.datetime):
            occurred_at = self.occurred_at.isoformat()
        else:
            occurred_at = self.occurred_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "clientEventId": client_event_id,
                "type": type_,
                "payload": payload,
            }
        )
        if occurred_at is not UNSET:
            field_dict["occurredAt"] = occurred_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.batch_event_payload import BatchEventPayload

        d = dict(src_dict)
        client_event_id = d.pop("clientEventId")

        type_ = d.pop("type")

        payload = BatchEventPayload.from_dict(d.pop("payload"))

        def _parse_occurred_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                occurred_at_type_0 = datetime.datetime.fromisoformat(data)

                return occurred_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        occurred_at = _parse_occurred_at(d.pop("occurredAt", UNSET))

        batch_event = cls(
            client_event_id=client_event_id,
            type_=type_,
            payload=payload,
            occurred_at=occurred_at,
        )

        batch_event.additional_properties = d
        return batch_event

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
