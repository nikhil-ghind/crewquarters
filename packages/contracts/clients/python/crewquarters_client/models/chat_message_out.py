from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.chat_message_out_role import ChatMessageOutRole
from ..models.chat_message_out_status import ChatMessageOutStatus

if TYPE_CHECKING:
    from ..models.chat_message_out_citations_item import ChatMessageOutCitationsItem
    from ..models.chat_message_out_usage_type_0 import ChatMessageOutUsageType0


T = TypeVar("T", bound="ChatMessageOut")


@_attrs_define
class ChatMessageOut:
    """
    Attributes:
        id (UUID):
        role (ChatMessageOutRole):
        content (str):
        status (ChatMessageOutStatus):
        citations (list[ChatMessageOutCitationsItem]):
        model (None | str):
        provider (None | str):
        usage (ChatMessageOutUsageType0 | None):
        created_at (datetime.datetime):
    """

    id: UUID
    role: ChatMessageOutRole
    content: str
    status: ChatMessageOutStatus
    citations: list[ChatMessageOutCitationsItem]
    model: str | None
    provider: str | None
    usage: ChatMessageOutUsageType0 | None
    created_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.chat_message_out_usage_type_0 import ChatMessageOutUsageType0

        id = str(self.id)

        role = self.role.value

        content = self.content

        status = self.status.value

        citations = []
        for citations_item_data in self.citations:
            citations_item = citations_item_data.to_dict()
            citations.append(citations_item)

        model: str | None
        model = self.model

        provider: str | None
        provider = self.provider

        usage: dict[str, Any] | None
        if isinstance(self.usage, ChatMessageOutUsageType0):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "role": role,
                "content": content,
                "status": status,
                "citations": citations,
                "model": model,
                "provider": provider,
                "usage": usage,
                "createdAt": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.chat_message_out_citations_item import (
            ChatMessageOutCitationsItem,
        )
        from ..models.chat_message_out_usage_type_0 import ChatMessageOutUsageType0

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        role = ChatMessageOutRole(d.pop("role"))

        content = d.pop("content")

        status = ChatMessageOutStatus(d.pop("status"))

        citations = []
        _citations = d.pop("citations")
        for citations_item_data in _citations:
            citations_item = ChatMessageOutCitationsItem.from_dict(citations_item_data)

            citations.append(citations_item)

        def _parse_model(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        model = _parse_model(d.pop("model"))

        def _parse_provider(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        provider = _parse_provider(d.pop("provider"))

        def _parse_usage(data: object) -> ChatMessageOutUsageType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = ChatMessageOutUsageType0.from_dict(data)

                return usage_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ChatMessageOutUsageType0 | None, data)

        usage = _parse_usage(d.pop("usage"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        chat_message_out = cls(
            id=id,
            role=role,
            content=content,
            status=status,
            citations=citations,
            model=model,
            provider=provider,
            usage=usage,
            created_at=created_at,
        )

        chat_message_out.additional_properties = d
        return chat_message_out

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
