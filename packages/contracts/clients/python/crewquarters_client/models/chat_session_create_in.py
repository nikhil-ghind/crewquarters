from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.chat_session_create_in_retrievalmode import ChatSessionCreateInRetrievalmode
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChatSessionCreateIn")


@_attrs_define
class ChatSessionCreateIn:
    """
    Attributes:
        title (None | str | Unset):
        model_profile (str | Unset): Local model variant. Default: 'local.general.small'.
        knowledge_base_id (None | Unset | UUID): Requires the knowledge service (Nikhil Sajan Khaneja, Person 3).
        retrieval_mode (ChatSessionCreateInRetrievalmode | Unset):  Default:
            ChatSessionCreateInRetrievalmode.WHEN_RELEVANT.
    """

    title: str | Unset | None = UNSET
    model_profile: str | Unset = "local.general.small"
    knowledge_base_id: Unset | UUID | None = UNSET
    retrieval_mode: ChatSessionCreateInRetrievalmode | Unset = (
        ChatSessionCreateInRetrievalmode.WHEN_RELEVANT
    )
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        title: str | Unset | None
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        model_profile = self.model_profile

        knowledge_base_id: str | Unset | None
        if isinstance(self.knowledge_base_id, Unset):
            knowledge_base_id = UNSET
        elif isinstance(self.knowledge_base_id, UUID):
            knowledge_base_id = str(self.knowledge_base_id)
        else:
            knowledge_base_id = self.knowledge_base_id

        retrieval_mode: str | Unset = UNSET
        if not isinstance(self.retrieval_mode, Unset):
            retrieval_mode = self.retrieval_mode.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if title is not UNSET:
            field_dict["title"] = title
        if model_profile is not UNSET:
            field_dict["modelProfile"] = model_profile
        if knowledge_base_id is not UNSET:
            field_dict["knowledgeBaseId"] = knowledge_base_id
        if retrieval_mode is not UNSET:
            field_dict["retrievalMode"] = retrieval_mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_title(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        model_profile = d.pop("modelProfile", UNSET)

        def _parse_knowledge_base_id(data: object) -> Unset | UUID | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                knowledge_base_id_type_0 = UUID(data)

                return knowledge_base_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        knowledge_base_id = _parse_knowledge_base_id(d.pop("knowledgeBaseId", UNSET))

        _retrieval_mode = d.pop("retrievalMode", UNSET)
        retrieval_mode: ChatSessionCreateInRetrievalmode | Unset
        if isinstance(_retrieval_mode, Unset):
            retrieval_mode = UNSET
        else:
            retrieval_mode = ChatSessionCreateInRetrievalmode(_retrieval_mode)

        chat_session_create_in = cls(
            title=title,
            model_profile=model_profile,
            knowledge_base_id=knowledge_base_id,
            retrieval_mode=retrieval_mode,
        )

        chat_session_create_in.additional_properties = d
        return chat_session_create_in

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
