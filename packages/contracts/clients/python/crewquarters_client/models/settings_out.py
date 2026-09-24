from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.settings_out_setupstate import SettingsOutSetupstate
    from ..models.settings_out_versions import SettingsOutVersions


T = TypeVar("T", bound="SettingsOut")


@_attrs_define
class SettingsOut:
    """
    Attributes:
        timezone (str):
        idle_unload_seconds (int):
        callback_base_url (None | str):
        setup_completed (bool):
        setup_state (SettingsOutSetupstate): Server-side first-run wizard progress (resumes after refresh/OAuth).
        versions (SettingsOutVersions): Per-setting version for optimistic updates.
    """

    timezone: str
    idle_unload_seconds: int
    callback_base_url: str | None
    setup_completed: bool
    setup_state: SettingsOutSetupstate
    versions: SettingsOutVersions
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        timezone = self.timezone

        idle_unload_seconds = self.idle_unload_seconds

        callback_base_url: str | None
        callback_base_url = self.callback_base_url

        setup_completed = self.setup_completed

        setup_state = self.setup_state.to_dict()

        versions = self.versions.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "timezone": timezone,
                "idleUnloadSeconds": idle_unload_seconds,
                "callbackBaseUrl": callback_base_url,
                "setupCompleted": setup_completed,
                "setupState": setup_state,
                "versions": versions,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.settings_out_setupstate import SettingsOutSetupstate
        from ..models.settings_out_versions import SettingsOutVersions

        d = dict(src_dict)
        timezone = d.pop("timezone")

        idle_unload_seconds = d.pop("idleUnloadSeconds")

        def _parse_callback_base_url(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        callback_base_url = _parse_callback_base_url(d.pop("callbackBaseUrl"))

        setup_completed = d.pop("setupCompleted")

        setup_state = SettingsOutSetupstate.from_dict(d.pop("setupState"))

        versions = SettingsOutVersions.from_dict(d.pop("versions"))

        settings_out = cls(
            timezone=timezone,
            idle_unload_seconds=idle_unload_seconds,
            callback_base_url=callback_base_url,
            setup_completed=setup_completed,
            setup_state=setup_state,
            versions=versions,
        )

        settings_out.additional_properties = d
        return settings_out

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
