from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.audit_event_out_metadata import AuditEventOutMetadata


T = TypeVar("T", bound="AuditEventOut")


@_attrs_define
class AuditEventOut:
    """
    Attributes:
        id (UUID):
        actor_type (str):
        actor_id (None | str):
        action (str):
        target_type (None | str):
        target_id (None | str):
        outcome (str):
        request_id (None | str):
        metadata (AuditEventOutMetadata):
        created_at (datetime.datetime):
    """

    id: UUID
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    metadata: AuditEventOutMetadata
    created_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        actor_type = self.actor_type

        actor_id: str | None
        actor_id = self.actor_id

        action = self.action

        target_type: str | None
        target_type = self.target_type

        target_id: str | None
        target_id = self.target_id

        outcome = self.outcome

        request_id: str | None
        request_id = self.request_id

        metadata = self.metadata.to_dict()

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "actorType": actor_type,
                "actorId": actor_id,
                "action": action,
                "targetType": target_type,
                "targetId": target_id,
                "outcome": outcome,
                "requestId": request_id,
                "metadata": metadata,
                "createdAt": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.audit_event_out_metadata import AuditEventOutMetadata

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        actor_type = d.pop("actorType")

        def _parse_actor_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        actor_id = _parse_actor_id(d.pop("actorId"))

        action = d.pop("action")

        def _parse_target_type(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        target_type = _parse_target_type(d.pop("targetType"))

        def _parse_target_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        target_id = _parse_target_id(d.pop("targetId"))

        outcome = d.pop("outcome")

        def _parse_request_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        request_id = _parse_request_id(d.pop("requestId"))

        metadata = AuditEventOutMetadata.from_dict(d.pop("metadata"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        audit_event_out = cls(
            id=id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            request_id=request_id,
            metadata=metadata,
            created_at=created_at,
        )

        audit_event_out.additional_properties = d
        return audit_event_out

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
