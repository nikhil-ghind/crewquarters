from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.voice_turn_out_role import VoiceTurnOutRole
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.voice_turn_out_timings import VoiceTurnOutTimings


T = TypeVar("T", bound="VoiceTurnOut")


@_attrs_define
class VoiceTurnOut:
    """
    Attributes:
        role (VoiceTurnOutRole):
        text (str):
        at (datetime.datetime):
        interrupted (bool | Unset):  Default: False.
        timings (VoiceTurnOutTimings | Unset): asrMs, llmFirstTokenMs, firstAudioMs (from the end of the caller's
            speech).
    """

    role: VoiceTurnOutRole
    text: str
    at: datetime.datetime
    interrupted: bool | Unset = False
    timings: VoiceTurnOutTimings | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        role = self.role.value

        text = self.text

        at = self.at.isoformat()

        interrupted = self.interrupted

        timings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.timings, Unset):
            timings = self.timings.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "role": role,
                "text": text,
                "at": at,
            }
        )
        if interrupted is not UNSET:
            field_dict["interrupted"] = interrupted
        if timings is not UNSET:
            field_dict["timings"] = timings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.voice_turn_out_timings import VoiceTurnOutTimings

        d = dict(src_dict)
        role = VoiceTurnOutRole(d.pop("role"))

        text = d.pop("text")

        at = datetime.datetime.fromisoformat(d.pop("at"))

        interrupted = d.pop("interrupted", UNSET)

        _timings = d.pop("timings", UNSET)
        timings: VoiceTurnOutTimings | Unset
        if isinstance(_timings, Unset):
            timings = UNSET
        else:
            timings = VoiceTurnOutTimings.from_dict(_timings)

        voice_turn_out = cls(
            role=role,
            text=text,
            at=at,
            interrupted=interrupted,
            timings=timings,
        )

        voice_turn_out.additional_properties = d
        return voice_turn_out

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
