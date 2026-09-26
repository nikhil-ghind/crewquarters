from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost")


@_attrs_define
class BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost:
    """
    Attributes:
        file (str): .wav, .flac, .mp3, .ogg, .m4a, or .webm
        language (None | str | Unset): ISO 639-1 hint such as 'en' or 'zh'.
    """

    file: str
    language: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file = self.file

        language: str | Unset | None
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file": file,
            }
        )
        if language is not UNSET:
            field_dict["language"] = language

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", (None, str(self.file).encode(), "text/plain")))

        if not isinstance(self.language, Unset):
            if isinstance(self.language, str):
                files.append(("language", (None, str(self.language).encode(), "text/plain")))
            else:
                files.append(("language", (None, str(self.language).encode(), "text/plain")))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        file = d.pop("file")

        def _parse_language(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        body_transcribe_audio_api_v1_models_model_id_transcriptions_post = cls(
            file=file,
            language=language,
        )

        body_transcribe_audio_api_v1_models_model_id_transcriptions_post.additional_properties = d
        return body_transcribe_audio_api_v1_models_model_id_transcriptions_post

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
