from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_event_out_payload import RunEventOutPayload


T = TypeVar("T", bound="RunEventOut")


@_attrs_define
class RunEventOut:
    """
    Attributes:
        run_id (UUID):
        sequence (int):
        attempt (int):
        type_ (str):
        payload (RunEventOutPayload):
        created_at (datetime.datetime):
        occurred_at (datetime.datetime | None | Unset): When the agent emitted the event (agent events only).
    """

    run_id: UUID
    sequence: int
    attempt: int
    type_: str
    payload: RunEventOutPayload
    created_at: datetime.datetime
    occurred_at: datetime.datetime | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        run_id = str(self.run_id)

        sequence = self.sequence

        attempt = self.attempt

        type_ = self.type_

        payload = self.payload.to_dict()

        created_at = self.created_at.isoformat()

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
                "runId": run_id,
                "sequence": sequence,
                "attempt": attempt,
                "type": type_,
                "payload": payload,
                "createdAt": created_at,
            }
        )
        if occurred_at is not UNSET:
            field_dict["occurredAt"] = occurred_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_event_out_payload import RunEventOutPayload

        d = dict(src_dict)
        run_id = UUID(d.pop("runId"))

        sequence = d.pop("sequence")

        attempt = d.pop("attempt")

        type_ = d.pop("type")

        payload = RunEventOutPayload.from_dict(d.pop("payload"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

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

        run_event_out = cls(
            run_id=run_id,
            sequence=sequence,
            attempt=attempt,
            type_=type_,
            payload=payload,
            created_at=created_at,
            occurred_at=occurred_at,
        )

        run_event_out.additional_properties = d
        return run_event_out

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
