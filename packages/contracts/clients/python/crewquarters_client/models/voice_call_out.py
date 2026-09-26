from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.voice_call_out_state import VoiceCallOutState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.voice_turn_out import VoiceTurnOut


T = TypeVar("T", bound="VoiceCallOut")


@_attrs_define
class VoiceCallOut:
    """A realtime voice call. The transcript lives in memory only, for this live view.

    Attributes:
        id (str):
        state (VoiceCallOutState):
        to (str): Masked destination.
        voice (str):
        created_at (datetime.datetime):
        simulated (bool | Unset):  Default: False.
        connected_at (datetime.datetime | None | Unset):
        ended_at (datetime.datetime | None | Unset):
        duration_seconds (int | None | Unset):
        end_reason (None | str | Unset):
        error (None | str | Unset):
        turns (list[VoiceTurnOut] | Unset):
    """

    id: str
    state: VoiceCallOutState
    to: str
    voice: str
    created_at: datetime.datetime
    simulated: bool | Unset = False
    connected_at: datetime.datetime | Unset | None = UNSET
    ended_at: datetime.datetime | Unset | None = UNSET
    duration_seconds: int | Unset | None = UNSET
    end_reason: str | Unset | None = UNSET
    error: str | Unset | None = UNSET
    turns: list[VoiceTurnOut] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        state = self.state.value

        to = self.to

        voice = self.voice

        created_at = self.created_at.isoformat()

        simulated = self.simulated

        connected_at: str | Unset | None
        if isinstance(self.connected_at, Unset):
            connected_at = UNSET
        elif isinstance(self.connected_at, datetime.datetime):
            connected_at = self.connected_at.isoformat()
        else:
            connected_at = self.connected_at

        ended_at: str | Unset | None
        if isinstance(self.ended_at, Unset):
            ended_at = UNSET
        elif isinstance(self.ended_at, datetime.datetime):
            ended_at = self.ended_at.isoformat()
        else:
            ended_at = self.ended_at

        duration_seconds: int | Unset | None
        if isinstance(self.duration_seconds, Unset):
            duration_seconds = UNSET
        else:
            duration_seconds = self.duration_seconds

        end_reason: str | Unset | None
        if isinstance(self.end_reason, Unset):
            end_reason = UNSET
        else:
            end_reason = self.end_reason

        error: str | Unset | None
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        turns: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.turns, Unset):
            turns = []
            for turns_item_data in self.turns:
                turns_item = turns_item_data.to_dict()
                turns.append(turns_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "state": state,
                "to": to,
                "voice": voice,
                "createdAt": created_at,
            }
        )
        if simulated is not UNSET:
            field_dict["simulated"] = simulated
        if connected_at is not UNSET:
            field_dict["connectedAt"] = connected_at
        if ended_at is not UNSET:
            field_dict["endedAt"] = ended_at
        if duration_seconds is not UNSET:
            field_dict["durationSeconds"] = duration_seconds
        if end_reason is not UNSET:
            field_dict["endReason"] = end_reason
        if error is not UNSET:
            field_dict["error"] = error
        if turns is not UNSET:
            field_dict["turns"] = turns

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.voice_turn_out import VoiceTurnOut

        d = dict(src_dict)
        id = d.pop("id")

        state = VoiceCallOutState(d.pop("state"))

        to = d.pop("to")

        voice = d.pop("voice")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        simulated = d.pop("simulated", UNSET)

        def _parse_connected_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                connected_at_type_0 = datetime.datetime.fromisoformat(data)

                return connected_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        connected_at = _parse_connected_at(d.pop("connectedAt", UNSET))

        def _parse_ended_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ended_at_type_0 = datetime.datetime.fromisoformat(data)

                return ended_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        ended_at = _parse_ended_at(d.pop("endedAt", UNSET))

        def _parse_duration_seconds(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        duration_seconds = _parse_duration_seconds(d.pop("durationSeconds", UNSET))

        def _parse_end_reason(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        end_reason = _parse_end_reason(d.pop("endReason", UNSET))

        def _parse_error(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        _turns = d.pop("turns", UNSET)
        turns: list[VoiceTurnOut] | Unset = UNSET
        if _turns is not UNSET:
            turns = []
            for turns_item_data in _turns:
                turns_item = VoiceTurnOut.from_dict(turns_item_data)

                turns.append(turns_item)

        voice_call_out = cls(
            id=id,
            state=state,
            to=to,
            voice=voice,
            created_at=created_at,
            simulated=simulated,
            connected_at=connected_at,
            ended_at=ended_at,
            duration_seconds=duration_seconds,
            end_reason=end_reason,
            error=error,
            turns=turns,
        )

        voice_call_out.additional_properties = d
        return voice_call_out

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
