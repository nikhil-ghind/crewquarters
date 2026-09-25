from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.knowledge_filters import KnowledgeFilters


T = TypeVar("T", bound="KnowledgeQueryIn")


@_attrs_define
class KnowledgeQueryIn:
    """
    Attributes:
        query (str):
        top_k (int | Unset):  Default: 8.
        max_context_tokens (int | Unset):  Default: 5000.
        filters (KnowledgeFilters | Unset):
    """

    query: str
    top_k: int | Unset = 8
    max_context_tokens: int | Unset = 5000
    filters: KnowledgeFilters | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        top_k = self.top_k

        max_context_tokens = self.max_context_tokens

        filters: dict[str, Any] | Unset = UNSET
        if not isinstance(self.filters, Unset):
            filters = self.filters.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "query": query,
            }
        )
        if top_k is not UNSET:
            field_dict["topK"] = top_k
        if max_context_tokens is not UNSET:
            field_dict["maxContextTokens"] = max_context_tokens
        if filters is not UNSET:
            field_dict["filters"] = filters

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.knowledge_filters import KnowledgeFilters

        d = dict(src_dict)
        query = d.pop("query")

        top_k = d.pop("topK", UNSET)

        max_context_tokens = d.pop("maxContextTokens", UNSET)

        _filters = d.pop("filters", UNSET)
        filters: KnowledgeFilters | Unset
        if isinstance(_filters, Unset):
            filters = UNSET
        else:
            filters = KnowledgeFilters.from_dict(_filters)

        knowledge_query_in = cls(
            query=query,
            top_k=top_k,
            max_context_tokens=max_context_tokens,
            filters=filters,
        )

        knowledge_query_in.additional_properties = d
        return knowledge_query_in

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
