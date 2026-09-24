from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.input_request_out_state import InputRequestOutState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.input_request_out_preview_type_0 import InputRequestOutPreviewType0
    from ..models.input_request_out_schema import InputRequestOutSchema


T = TypeVar("T", bound="InputRequestOut")


@_attrs_define
class InputRequestOut:
    """
    Attributes:
        id (UUID):
        run_id (UUID):
        key (str):
        title (str):
        prompt (str):
        schema (InputRequestOutSchema):
        preview (InputRequestOutPreviewType0 | None):
        state (InputRequestOutState):
        deadline (datetime.datetime):
        version (int):
        created_at (datetime.datetime):
        answered_at (datetime.datetime | None):
        agent_name (None | str | Unset):
        answer (Any | None | Unset):
    """

    id: UUID
    run_id: UUID
    key: str
    title: str
    prompt: str
    schema: InputRequestOutSchema
    preview: InputRequestOutPreviewType0 | None
    state: InputRequestOutState
    deadline: datetime.datetime
    version: int
    created_at: datetime.datetime
    answered_at: datetime.datetime | None
    agent_name: str | Unset | None = UNSET
    answer: Any | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.input_request_out_preview_type_0 import (
            InputRequestOutPreviewType0,
        )

        id = str(self.id)

        run_id = str(self.run_id)

        key = self.key

        title = self.title

        prompt = self.prompt

        schema = self.schema.to_dict()

        preview: dict[str, Any] | None
        if isinstance(self.preview, InputRequestOutPreviewType0):
            preview = self.preview.to_dict()
        else:
            preview = self.preview

        state = self.state.value

        deadline = self.deadline.isoformat()

        version = self.version

        created_at = self.created_at.isoformat()

        answered_at: str | None
        if isinstance(self.answered_at, datetime.datetime):
            answered_at = self.answered_at.isoformat()
        else:
            answered_at = self.answered_at

        agent_name: str | Unset | None
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        answer: Any | Unset | None
        if isinstance(self.answer, Unset):
            answer = UNSET
        else:
            answer = self.answer

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "runId": run_id,
                "key": key,
                "title": title,
                "prompt": prompt,
                "schema": schema,
                "preview": preview,
                "state": state,
                "deadline": deadline,
                "version": version,
                "createdAt": created_at,
                "answeredAt": answered_at,
            }
        )
        if agent_name is not UNSET:
            field_dict["agentName"] = agent_name
        if answer is not UNSET:
            field_dict["answer"] = answer

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.input_request_out_preview_type_0 import (
            InputRequestOutPreviewType0,
        )
        from ..models.input_request_out_schema import InputRequestOutSchema

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        run_id = UUID(d.pop("runId"))

        key = d.pop("key")

        title = d.pop("title")

        prompt = d.pop("prompt")

        schema = InputRequestOutSchema.from_dict(d.pop("schema"))

        def _parse_preview(data: object) -> InputRequestOutPreviewType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                preview_type_0 = InputRequestOutPreviewType0.from_dict(data)

                return preview_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InputRequestOutPreviewType0 | None, data)

        preview = _parse_preview(d.pop("preview"))

        state = InputRequestOutState(d.pop("state"))

        deadline = datetime.datetime.fromisoformat(d.pop("deadline"))

        version = d.pop("version")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        def _parse_answered_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answered_at_type_0 = datetime.datetime.fromisoformat(data)

                return answered_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        answered_at = _parse_answered_at(d.pop("answeredAt"))

        def _parse_agent_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agentName", UNSET))

        def _parse_answer(data: object) -> Any | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        answer = _parse_answer(d.pop("answer", UNSET))

        input_request_out = cls(
            id=id,
            run_id=run_id,
            key=key,
            title=title,
            prompt=prompt,
            schema=schema,
            preview=preview,
            state=state,
            deadline=deadline,
            version=version,
            created_at=created_at,
            answered_at=answered_at,
            agent_name=agent_name,
            answer=answer,
        )

        input_request_out.additional_properties = d
        return input_request_out

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
