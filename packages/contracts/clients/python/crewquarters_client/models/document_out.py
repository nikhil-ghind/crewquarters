from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.document_out_state import DocumentOutState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.document_out_error_type_0 import DocumentOutErrorType0
    from ..models.document_out_extracted_type_0 import DocumentOutExtractedType0


T = TypeVar("T", bound="DocumentOut")


@_attrs_define
class DocumentOut:
    """
    Attributes:
        id (UUID):
        knowledge_base_id (UUID):
        name (str):
        mime (str):
        bytes_ (int):
        sha256 (str):
        state (DocumentOutState):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        extracted (DocumentOutExtractedType0 | None | Unset): Extraction summary.
        error (DocumentOutErrorType0 | None | Unset): {code, message} when FAILED.
    """

    id: UUID
    knowledge_base_id: UUID
    name: str
    mime: str
    bytes_: int
    sha256: str
    state: DocumentOutState
    created_at: datetime.datetime
    updated_at: datetime.datetime
    extracted: DocumentOutExtractedType0 | Unset | None = UNSET
    error: DocumentOutErrorType0 | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.document_out_error_type_0 import DocumentOutErrorType0
        from ..models.document_out_extracted_type_0 import (
            DocumentOutExtractedType0,
        )

        id = str(self.id)

        knowledge_base_id = str(self.knowledge_base_id)

        name = self.name

        mime = self.mime

        bytes_ = self.bytes_

        sha256 = self.sha256

        state = self.state.value

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        extracted: dict[str, Any] | Unset | None
        if isinstance(self.extracted, Unset):
            extracted = UNSET
        elif isinstance(self.extracted, DocumentOutExtractedType0):
            extracted = self.extracted.to_dict()
        else:
            extracted = self.extracted

        error: dict[str, Any] | Unset | None
        if isinstance(self.error, Unset):
            error = UNSET
        elif isinstance(self.error, DocumentOutErrorType0):
            error = self.error.to_dict()
        else:
            error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "knowledgeBaseId": knowledge_base_id,
                "name": name,
                "mime": mime,
                "bytes": bytes_,
                "sha256": sha256,
                "state": state,
                "createdAt": created_at,
                "updatedAt": updated_at,
            }
        )
        if extracted is not UNSET:
            field_dict["extracted"] = extracted
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.document_out_error_type_0 import DocumentOutErrorType0
        from ..models.document_out_extracted_type_0 import (
            DocumentOutExtractedType0,
        )

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        knowledge_base_id = UUID(d.pop("knowledgeBaseId"))

        name = d.pop("name")

        mime = d.pop("mime")

        bytes_ = d.pop("bytes")

        sha256 = d.pop("sha256")

        state = DocumentOutState(d.pop("state"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updatedAt"))

        def _parse_extracted(data: object) -> DocumentOutExtractedType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                extracted_type_0 = DocumentOutExtractedType0.from_dict(data)

                return extracted_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DocumentOutExtractedType0 | None | Unset, data)

        extracted = _parse_extracted(d.pop("extracted", UNSET))

        def _parse_error(data: object) -> DocumentOutErrorType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                error_type_0 = DocumentOutErrorType0.from_dict(data)

                return error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DocumentOutErrorType0 | None | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        document_out = cls(
            id=id,
            knowledge_base_id=knowledge_base_id,
            name=name,
            mime=mime,
            bytes_=bytes_,
            sha256=sha256,
            state=state,
            created_at=created_at,
            updated_at=updated_at,
            extracted=extracted,
            error=error,
        )

        document_out.additional_properties = d
        return document_out

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
