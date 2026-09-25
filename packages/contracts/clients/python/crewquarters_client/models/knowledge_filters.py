from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="KnowledgeFilters")


@_attrs_define
class KnowledgeFilters:
    """
    Attributes:
        document_ids (list[UUID] | Unset):
    """

    document_ids: list[UUID] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        document_ids: list[str] | Unset = UNSET
        if not isinstance(self.document_ids, Unset):
            document_ids = []
            for document_ids_item_data in self.document_ids:
                document_ids_item = str(document_ids_item_data)
                document_ids.append(document_ids_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if document_ids is not UNSET:
            field_dict["documentIds"] = document_ids

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _document_ids = d.pop("documentIds", UNSET)
        document_ids: list[UUID] | Unset = UNSET
        if _document_ids is not UNSET:
            document_ids = []
            for document_ids_item_data in _document_ids:
                document_ids_item = UUID(document_ids_item_data)

                document_ids.append(document_ids_item)

        knowledge_filters = cls(
            document_ids=document_ids,
        )

        knowledge_filters.additional_properties = d
        return knowledge_filters

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
