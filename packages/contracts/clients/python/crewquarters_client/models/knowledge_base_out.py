from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="KnowledgeBaseOut")


@_attrs_define
class KnowledgeBaseOut:
    """
    Attributes:
        id (UUID):
        name (str):
        embedding_profile (str):
        embedding_dimension (int):
        created_at (datetime.datetime):
    """

    id: UUID
    name: str
    embedding_profile: str
    embedding_dimension: int
    created_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        name = self.name

        embedding_profile = self.embedding_profile

        embedding_dimension = self.embedding_dimension

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "embeddingProfile": embedding_profile,
                "embeddingDimension": embedding_dimension,
                "createdAt": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        embedding_profile = d.pop("embeddingProfile")

        embedding_dimension = d.pop("embeddingDimension")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        knowledge_base_out = cls(
            id=id,
            name=name,
            embedding_profile=embedding_profile,
            embedding_dimension=embedding_dimension,
            created_at=created_at,
        )

        knowledge_base_out.additional_properties = d
        return knowledge_base_out

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
