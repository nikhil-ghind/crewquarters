from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.settings_patch_in_setup_state_type_0 import SettingsPatchInSetupStateType0
    from ..models.settings_patch_in_versions import SettingsPatchInVersions


T = TypeVar("T", bound="SettingsPatchIn")


@_attrs_define
class SettingsPatchIn:
    """
    Attributes:
        timezone (None | str | Unset):
        idle_unload_seconds (int | None | Unset):
        callback_base_url (None | str | Unset): Read-only; set CQ_PUBLIC_BASE_URL instead. Sending it returns 422
            SETTING_READ_ONLY.
        setup_completed (bool | None | Unset):
        setup_state (None | SettingsPatchInSetupStateType0 | Unset):
        versions (SettingsPatchInVersions | Unset): Expected versions; mismatches return 409.
    """

    timezone: str | Unset | None = UNSET
    idle_unload_seconds: int | Unset | None = UNSET
    callback_base_url: str | Unset | None = UNSET
    setup_completed: bool | Unset | None = UNSET
    setup_state: SettingsPatchInSetupStateType0 | Unset | None = UNSET
    versions: SettingsPatchInVersions | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.settings_patch_in_setup_state_type_0 import (
            SettingsPatchInSetupStateType0,
        )

        timezone: str | Unset | None
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        idle_unload_seconds: int | Unset | None
        if isinstance(self.idle_unload_seconds, Unset):
            idle_unload_seconds = UNSET
        else:
            idle_unload_seconds = self.idle_unload_seconds

        callback_base_url: str | Unset | None
        if isinstance(self.callback_base_url, Unset):
            callback_base_url = UNSET
        else:
            callback_base_url = self.callback_base_url

        setup_completed: bool | Unset | None
        if isinstance(self.setup_completed, Unset):
            setup_completed = UNSET
        else:
            setup_completed = self.setup_completed

        setup_state: dict[str, Any] | Unset | None
        if isinstance(self.setup_state, Unset):
            setup_state = UNSET
        elif isinstance(self.setup_state, SettingsPatchInSetupStateType0):
            setup_state = self.setup_state.to_dict()
        else:
            setup_state = self.setup_state

        versions: dict[str, Any] | Unset = UNSET
        if not isinstance(self.versions, Unset):
            versions = self.versions.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if idle_unload_seconds is not UNSET:
            field_dict["idleUnloadSeconds"] = idle_unload_seconds
        if callback_base_url is not UNSET:
            field_dict["callbackBaseUrl"] = callback_base_url
        if setup_completed is not UNSET:
            field_dict["setupCompleted"] = setup_completed
        if setup_state is not UNSET:
            field_dict["setupState"] = setup_state
        if versions is not UNSET:
            field_dict["versions"] = versions

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.settings_patch_in_setup_state_type_0 import (
            SettingsPatchInSetupStateType0,
        )
        from ..models.settings_patch_in_versions import SettingsPatchInVersions

        d = dict(src_dict)

        def _parse_timezone(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        def _parse_idle_unload_seconds(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        idle_unload_seconds = _parse_idle_unload_seconds(d.pop("idleUnloadSeconds", UNSET))

        def _parse_callback_base_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        callback_base_url = _parse_callback_base_url(d.pop("callbackBaseUrl", UNSET))

        def _parse_setup_completed(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        setup_completed = _parse_setup_completed(d.pop("setupCompleted", UNSET))

        def _parse_setup_state(data: object) -> SettingsPatchInSetupStateType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                setup_state_type_0 = SettingsPatchInSetupStateType0.from_dict(data)

                return setup_state_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SettingsPatchInSetupStateType0 | Unset, data)

        setup_state = _parse_setup_state(d.pop("setupState", UNSET))

        _versions = d.pop("versions", UNSET)
        versions: SettingsPatchInVersions | Unset
        if isinstance(_versions, Unset):
            versions = UNSET
        else:
            versions = SettingsPatchInVersions.from_dict(_versions)

        settings_patch_in = cls(
            timezone=timezone,
            idle_unload_seconds=idle_unload_seconds,
            callback_base_url=callback_base_url,
            setup_completed=setup_completed,
            setup_state=setup_state,
            versions=versions,
        )

        settings_patch_in.additional_properties = d
        return settings_patch_in

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
