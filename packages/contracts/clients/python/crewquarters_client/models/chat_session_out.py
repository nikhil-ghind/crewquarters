from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ChatSessionOut")


@_attrs_define
class ChatSessionOut:
    """
    Attributes:
        id (UUID):
        title (str):
        model_profile (str):
        knowledge_base_id (None | UUID):
        retrieval_mode (str):
        enabled (bool):
        holds_model_lease (bool):
        version (int):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        last_message_at (datetime.datetime | None):
        local (bool | Unset): Chat never leaves the device. Default: True.
    """

    id: UUID
    title: str
    model_profile: str
    knowledge_base_id: UUID | None
    retrieval_mode: str
    enabled: bool
    holds_model_lease: bool
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_message_at: datetime.datetime | None
    local: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        title = self.title

        model_profile = self.model_profile

        knowledge_base_id: str | None
        if isinstance(self.knowledge_base_id, UUID):
            knowledge_base_id = str(self.knowledge_base_id)
        else:
            knowledge_base_id = self.knowledge_base_id

        retrieval_mode = self.retrieval_mode

        enabled = self.enabled

        holds_model_lease = self.holds_model_lease

        version = self.version

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        last_message_at: str | None
        if isinstance(self.last_message_at, datetime.datetime):
            last_message_at = self.last_message_at.isoformat()
        else:
            last_message_at = self.last_message_at

        local = self.local

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "title": title,
                "modelProfile": model_profile,
                "knowledgeBaseId": knowledge_base_id,
                "retrievalMode": retrieval_mode,
                "enabled": enabled,
                "holdsModelLease": holds_model_lease,
                "version": version,
                "createdAt": created_at,
                "updatedAt": updated_at,
                "lastMessageAt": last_message_at,
            }
        )
        if local is not UNSET:
            field_dict["local"] = local

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        title = d.pop("title")

        model_profile = d.pop("modelProfile")

        def _parse_knowledge_base_id(data: object) -> UUID | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                knowledge_base_id_type_0 = UUID(data)

                return knowledge_base_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        knowledge_base_id = _parse_knowledge_base_id(d.pop("knowledgeBaseId"))

        retrieval_mode = d.pop("retrievalMode")

        enabled = d.pop("enabled")

        holds_model_lease = d.pop("holdsModelLease")

        version = d.pop("version")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updatedAt"))

        def _parse_last_message_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_message_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_message_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_message_at = _parse_last_message_at(d.pop("lastMessageAt"))

        local = d.pop("local", UNSET)

        chat_session_out = cls(
            id=id,
            title=title,
            model_profile=model_profile,
            knowledge_base_id=knowledge_base_id,
            retrieval_mode=retrieval_mode,
            enabled=enabled,
            holds_model_lease=holds_model_lease,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
            last_message_at=last_message_at,
            local=local,
        )

        chat_session_out.additional_properties = d
        return chat_session_out

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
