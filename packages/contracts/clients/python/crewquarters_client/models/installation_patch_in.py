from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.installation_patch_in_approved_permissions_type_0 import (
        InstallationPatchInApprovedPermissionsType0,
    )
    from ..models.installation_patch_in_config_type_0 import InstallationPatchInConfigType0
    from ..models.installation_patch_in_model_bindings_type_0 import (
        InstallationPatchInModelBindingsType0,
    )


T = TypeVar("T", bound="InstallationPatchIn")


@_attrs_define
class InstallationPatchIn:
    """
    Attributes:
        version (int): Current installation version (optimistic concurrency).
        agent_version (None | str | Unset): Upgrade/downgrade to this agent version.
        config (InstallationPatchInConfigType0 | None | Unset):
        approved_permissions (InstallationPatchInApprovedPermissionsType0 | None | Unset):
        model_bindings (InstallationPatchInModelBindingsType0 | None | Unset):
        enabled (bool | None | Unset):
    """

    version: int
    agent_version: str | Unset | None = UNSET
    config: InstallationPatchInConfigType0 | Unset | None = UNSET
    approved_permissions: InstallationPatchInApprovedPermissionsType0 | Unset | None = UNSET
    model_bindings: InstallationPatchInModelBindingsType0 | Unset | None = UNSET
    enabled: bool | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.installation_patch_in_approved_permissions_type_0 import (
            InstallationPatchInApprovedPermissionsType0,
        )
        from ..models.installation_patch_in_config_type_0 import (
            InstallationPatchInConfigType0,
        )
        from ..models.installation_patch_in_model_bindings_type_0 import (
            InstallationPatchInModelBindingsType0,
        )

        version = self.version

        agent_version: str | Unset | None
        if isinstance(self.agent_version, Unset):
            agent_version = UNSET
        else:
            agent_version = self.agent_version

        config: dict[str, Any] | Unset | None
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, InstallationPatchInConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        approved_permissions: dict[str, Any] | Unset | None
        if isinstance(self.approved_permissions, Unset):
            approved_permissions = UNSET
        elif isinstance(self.approved_permissions, InstallationPatchInApprovedPermissionsType0):
            approved_permissions = self.approved_permissions.to_dict()
        else:
            approved_permissions = self.approved_permissions

        model_bindings: dict[str, Any] | Unset | None
        if isinstance(self.model_bindings, Unset):
            model_bindings = UNSET
        elif isinstance(self.model_bindings, InstallationPatchInModelBindingsType0):
            model_bindings = self.model_bindings.to_dict()
        else:
            model_bindings = self.model_bindings

        enabled: bool | Unset | None
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "version": version,
            }
        )
        if agent_version is not UNSET:
            field_dict["agentVersion"] = agent_version
        if config is not UNSET:
            field_dict["config"] = config
        if approved_permissions is not UNSET:
            field_dict["approvedPermissions"] = approved_permissions
        if model_bindings is not UNSET:
            field_dict["modelBindings"] = model_bindings
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.installation_patch_in_approved_permissions_type_0 import (
            InstallationPatchInApprovedPermissionsType0,
        )
        from ..models.installation_patch_in_config_type_0 import (
            InstallationPatchInConfigType0,
        )
        from ..models.installation_patch_in_model_bindings_type_0 import (
            InstallationPatchInModelBindingsType0,
        )

        d = dict(src_dict)
        version = d.pop("version")

        def _parse_agent_version(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_version = _parse_agent_version(d.pop("agentVersion", UNSET))

        def _parse_config(data: object) -> InstallationPatchInConfigType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = InstallationPatchInConfigType0.from_dict(data)

                return config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstallationPatchInConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_approved_permissions(
            data: object,
        ) -> InstallationPatchInApprovedPermissionsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                approved_permissions_type_0 = InstallationPatchInApprovedPermissionsType0.from_dict(
                    data
                )

                return approved_permissions_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstallationPatchInApprovedPermissionsType0 | None | Unset, data)

        approved_permissions = _parse_approved_permissions(d.pop("approvedPermissions", UNSET))

        def _parse_model_bindings(
            data: object,
        ) -> InstallationPatchInModelBindingsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_bindings_type_0 = InstallationPatchInModelBindingsType0.from_dict(data)

                return model_bindings_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstallationPatchInModelBindingsType0 | None | Unset, data)

        model_bindings = _parse_model_bindings(d.pop("modelBindings", UNSET))

        def _parse_enabled(data: object) -> bool | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        installation_patch_in = cls(
            version=version,
            agent_version=agent_version,
            config=config,
            approved_permissions=approved_permissions,
            model_bindings=model_bindings,
            enabled=enabled,
        )

        installation_patch_in.additional_properties = d
        return installation_patch_in

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
