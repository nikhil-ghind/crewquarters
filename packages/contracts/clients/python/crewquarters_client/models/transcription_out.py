from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TranscriptionOut")


@_attrs_define
class TranscriptionOut:
    """Whole-file speech-to-text result. Neither the audio nor the transcript is stored.

    Attributes:
        model_id (str):
        text (str):
        language (None | str | Unset): The language hint that was sent, if any.
        audio_seconds (float | None | Unset): Audio duration the model reported.
        latency_ms (int | Unset): Time the model spent transcribing. Default: 0.
        request_id (None | str | Unset):
    """

    model_id: str
    text: str
    language: str | Unset | None = UNSET
    audio_seconds: float | Unset | None = UNSET
    latency_ms: int | Unset = 0
    request_id: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        text = self.text

        language: str | Unset | None
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        audio_seconds: float | Unset | None
        if isinstance(self.audio_seconds, Unset):
            audio_seconds = UNSET
        else:
            audio_seconds = self.audio_seconds

        latency_ms = self.latency_ms

        request_id: str | Unset | None
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "modelId": model_id,
                "text": text,
            }
        )
        if language is not UNSET:
            field_dict["language"] = language
        if audio_seconds is not UNSET:
            field_dict["audioSeconds"] = audio_seconds
        if latency_ms is not UNSET:
            field_dict["latencyMs"] = latency_ms
        if request_id is not UNSET:
            field_dict["requestId"] = request_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_id = d.pop("modelId")

        text = d.pop("text")

        def _parse_language(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        def _parse_audio_seconds(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        audio_seconds = _parse_audio_seconds(d.pop("audioSeconds", UNSET))

        latency_ms = d.pop("latencyMs", UNSET)

        def _parse_request_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("requestId", UNSET))

        transcription_out = cls(
            model_id=model_id,
            text=text,
            language=language,
            audio_seconds=audio_seconds,
            latency_ms=latency_ms,
            request_id=request_id,
        )

        transcription_out.additional_properties = d
        return transcription_out

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
