from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.passage_out import PassageOut


T = TypeVar("T", bound="KnowledgeQueryOut")


@_attrs_define
class KnowledgeQueryOut:
    """
    Attributes:
        knowledge_base_id (UUID):
        passages (list[PassageOut]):
    """

    knowledge_base_id: UUID
    passages: list[PassageOut]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        knowledge_base_id = str(self.knowledge_base_id)

        passages = []
        for passages_item_data in self.passages:
            passages_item = passages_item_data.to_dict()
            passages.append(passages_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "knowledgeBaseId": knowledge_base_id,
                "passages": passages,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.passage_out import PassageOut

        d = dict(src_dict)
        knowledge_base_id = UUID(d.pop("knowledgeBaseId"))

        passages = []
        _passages = d.pop("passages")
        for passages_item_data in _passages:
            passages_item = PassageOut.from_dict(passages_item_data)

            passages.append(passages_item)

        knowledge_query_out = cls(
            knowledge_base_id=knowledge_base_id,
            passages=passages,
        )

        knowledge_query_out.additional_properties = d
        return knowledge_query_out

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
