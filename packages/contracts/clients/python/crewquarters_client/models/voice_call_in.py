from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="VoiceCallIn")


@_attrs_define
class VoiceCallIn:
    """
    Attributes:
        to (str): A number in CQ_TWILIO_ALLOWED_NUMBERS.
        confirm (bool): Must be true: the owner confirmed a live call.
        voice (str | Unset): Speech voice id. Default: 'female'.
        instructions (str | Unset): Extra instructions for the assistant on this call. Default: ''.
    """

    to: str
    confirm: bool
    voice: str | Unset = "female"
    instructions: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        to = self.to

        confirm = self.confirm

        voice = self.voice

        instructions = self.instructions

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "to": to,
                "confirm": confirm,
            }
        )
        if voice is not UNSET:
            field_dict["voice"] = voice
        if instructions is not UNSET:
            field_dict["instructions"] = instructions

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        to = d.pop("to")

        confirm = d.pop("confirm")

        voice = d.pop("voice", UNSET)

        instructions = d.pop("instructions", UNSET)

        voice_call_in = cls(
            to=to,
            confirm=confirm,
            voice=voice,
            instructions=instructions,
        )

        voice_call_in.additional_properties = d
        return voice_call_in

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
