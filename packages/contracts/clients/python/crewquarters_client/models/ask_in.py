from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ask_in_preview_type_0 import AskInPreviewType0
    from ..models.ask_in_schema import AskInSchema


T = TypeVar("T", bound="AskIn")


@_attrs_define
class AskIn:
    """
    Attributes:
        attempt (int):
        key (str):
        title (str):
        prompt (str):
        schema (AskInSchema):
        timeout_seconds (int):
        preview (AskInPreviewType0 | None | Unset):
    """

    attempt: int
    key: str
    title: str
    prompt: str
    schema: AskInSchema
    timeout_seconds: int
    preview: AskInPreviewType0 | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ask_in_preview_type_0 import AskInPreviewType0

        attempt = self.attempt

        key = self.key

        title = self.title

        prompt = self.prompt

        schema = self.schema.to_dict()

        timeout_seconds = self.timeout_seconds

        preview: dict[str, Any] | Unset | None
        if isinstance(self.preview, Unset):
            preview = UNSET
        elif isinstance(self.preview, AskInPreviewType0):
            preview = self.preview.to_dict()
        else:
            preview = self.preview

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempt": attempt,
                "key": key,
                "title": title,
                "prompt": prompt,
                "schema": schema,
                "timeoutSeconds": timeout_seconds,
            }
        )
        if preview is not UNSET:
            field_dict["preview"] = preview

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ask_in_preview_type_0 import AskInPreviewType0
        from ..models.ask_in_schema import AskInSchema

        d = dict(src_dict)
        attempt = d.pop("attempt")

        key = d.pop("key")

        title = d.pop("title")

        prompt = d.pop("prompt")

        schema = AskInSchema.from_dict(d.pop("schema"))

        timeout_seconds = d.pop("timeoutSeconds")

        def _parse_preview(data: object) -> AskInPreviewType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                preview_type_0 = AskInPreviewType0.from_dict(data)

                return preview_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AskInPreviewType0 | None | Unset, data)

        preview = _parse_preview(d.pop("preview", UNSET))

        ask_in = cls(
            attempt=attempt,
            key=key,
            title=title,
            prompt=prompt,
            schema=schema,
            timeout_seconds=timeout_seconds,
            preview=preview,
        )

        ask_in.additional_properties = d
        return ask_in

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
