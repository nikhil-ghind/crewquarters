from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.attention_item_kind import AttentionItemKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="AttentionItem")


@_attrs_define
class AttentionItem:
    """
    Attributes:
        kind (AttentionItemKind):
        title (str):
        detail (str):
        run_id (None | Unset | UUID):
        input_request_id (None | Unset | UUID):
        schedule_id (None | Unset | UUID):
        model_id (None | str | Unset):
        created_at (datetime.datetime | None | Unset):
    """

    kind: AttentionItemKind
    title: str
    detail: str
    run_id: Unset | UUID | None = UNSET
    input_request_id: Unset | UUID | None = UNSET
    schedule_id: Unset | UUID | None = UNSET
    model_id: str | Unset | None = UNSET
    created_at: datetime.datetime | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        title = self.title

        detail = self.detail

        run_id: str | Unset | None
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        elif isinstance(self.run_id, UUID):
            run_id = str(self.run_id)
        else:
            run_id = self.run_id

        input_request_id: str | Unset | None
        if isinstance(self.input_request_id, Unset):
            input_request_id = UNSET
        elif isinstance(self.input_request_id, UUID):
            input_request_id = str(self.input_request_id)
        else:
            input_request_id = self.input_request_id

        schedule_id: str | Unset | None
        if isinstance(self.schedule_id, Unset):
            schedule_id = UNSET
        elif isinstance(self.schedule_id, UUID):
            schedule_id = str(self.schedule_id)
        else:
            schedule_id = self.schedule_id

        model_id: str | Unset | None
        if isinstance(self.model_id, Unset):
            model_id = UNSET
        else:
            model_id = self.model_id

        created_at: str | Unset | None
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
            }
        )
        if run_id is not UNSET:
            field_dict["runId"] = run_id
        if input_request_id is not UNSET:
            field_dict["inputRequestId"] = input_request_id
        if schedule_id is not UNSET:
            field_dict["scheduleId"] = schedule_id
        if model_id is not UNSET:
            field_dict["modelId"] = model_id
        if created_at is not UNSET:
            field_dict["createdAt"] = created_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = AttentionItemKind(d.pop("kind"))

        title = d.pop("title")

        detail = d.pop("detail")

        def _parse_run_id(data: object) -> Unset | UUID | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_id_type_0 = UUID(data)

                return run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        run_id = _parse_run_id(d.pop("runId", UNSET))

        def _parse_input_request_id(data: object) -> Unset | UUID | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                input_request_id_type_0 = UUID(data)

                return input_request_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        input_request_id = _parse_input_request_id(d.pop("inputRequestId", UNSET))

        def _parse_schedule_id(data: object) -> Unset | UUID | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                schedule_id_type_0 = UUID(data)

                return schedule_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        schedule_id = _parse_schedule_id(d.pop("scheduleId", UNSET))

        def _parse_model_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_id = _parse_model_id(d.pop("modelId", UNSET))

        def _parse_created_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = datetime.datetime.fromisoformat(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("createdAt", UNSET))

        attention_item = cls(
            kind=kind,
            title=title,
            detail=detail,
            run_id=run_id,
            input_request_id=input_request_id,
            schedule_id=schedule_id,
            model_id=model_id,
            created_at=created_at,
        )

        attention_item.additional_properties = d
        return attention_item

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
