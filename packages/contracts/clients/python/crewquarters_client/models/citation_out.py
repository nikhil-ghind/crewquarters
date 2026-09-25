from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.citation_out_locator import CitationOutLocator
    from ..models.passage_document import PassageDocument


T = TypeVar("T", bound="CitationOut")


@_attrs_define
class CitationOut:
    """A citation stored on an assistant message, with the document's current state.

    Attributes:
        index (int):
        citation_id (str):
        text (str): The passage as retrieved; untrusted, render escaped.
        document (PassageDocument):
        locator (CitationOutLocator):
        location (str):
        knowledge_base_id (UUID):
        document_available (bool): False once the document was deleted: its chunks no longer appear in chat.
        score (float | None | Unset):
        document_state (None | str | Unset):
    """

    index: int
    citation_id: str
    text: str
    document: PassageDocument
    locator: CitationOutLocator
    location: str
    knowledge_base_id: UUID
    document_available: bool
    score: float | Unset | None = UNSET
    document_state: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        index = self.index

        citation_id = self.citation_id

        text = self.text

        document = self.document.to_dict()

        locator = self.locator.to_dict()

        location = self.location

        knowledge_base_id = str(self.knowledge_base_id)

        document_available = self.document_available

        score: float | Unset | None
        if isinstance(self.score, Unset):
            score = UNSET
        else:
            score = self.score

        document_state: str | Unset | None
        if isinstance(self.document_state, Unset):
            document_state = UNSET
        else:
            document_state = self.document_state

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "index": index,
                "citationId": citation_id,
                "text": text,
                "document": document,
                "locator": locator,
                "location": location,
                "knowledgeBaseId": knowledge_base_id,
                "documentAvailable": document_available,
            }
        )
        if score is not UNSET:
            field_dict["score"] = score
        if document_state is not UNSET:
            field_dict["documentState"] = document_state

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.citation_out_locator import CitationOutLocator
        from ..models.passage_document import PassageDocument

        d = dict(src_dict)
        index = d.pop("index")

        citation_id = d.pop("citationId")

        text = d.pop("text")

        document = PassageDocument.from_dict(d.pop("document"))

        locator = CitationOutLocator.from_dict(d.pop("locator"))

        location = d.pop("location")

        knowledge_base_id = UUID(d.pop("knowledgeBaseId"))

        document_available = d.pop("documentAvailable")

        def _parse_score(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        score = _parse_score(d.pop("score", UNSET))

        def _parse_document_state(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_state = _parse_document_state(d.pop("documentState", UNSET))

        citation_out = cls(
            index=index,
            citation_id=citation_id,
            text=text,
            document=document,
            locator=locator,
            location=location,
            knowledge_base_id=knowledge_base_id,
            document_available=document_available,
            score=score,
            document_state=document_state,
        )

        citation_out.additional_properties = d
        return citation_out

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
