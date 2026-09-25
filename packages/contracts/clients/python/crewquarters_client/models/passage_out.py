from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.passage_document import PassageDocument
    from ..models.passage_out_locator import PassageOutLocator


T = TypeVar("T", bound="PassageOut")


@_attrs_define
class PassageOut:
    """
    Attributes:
        citation_id (str):
        text (str): Untrusted document text; render escaped.
        score (float):
        document (PassageDocument):
        locator (PassageOutLocator):
        location (str):
    """

    citation_id: str
    text: str
    score: float
    document: PassageDocument
    locator: PassageOutLocator
    location: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        citation_id = self.citation_id

        text = self.text

        score = self.score

        document = self.document.to_dict()

        locator = self.locator.to_dict()

        location = self.location

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "citationId": citation_id,
                "text": text,
                "score": score,
                "document": document,
                "locator": locator,
                "location": location,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.passage_document import PassageDocument
        from ..models.passage_out_locator import PassageOutLocator

        d = dict(src_dict)
        citation_id = d.pop("citationId")

        text = d.pop("text")

        score = d.pop("score")

        document = PassageDocument.from_dict(d.pop("document"))

        locator = PassageOutLocator.from_dict(d.pop("locator"))

        location = d.pop("location")

        passage_out = cls(
            citation_id=citation_id,
            text=text,
            score=score,
            document=document,
            locator=locator,
            location=location,
        )

        passage_out.additional_properties = d
        return passage_out

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
