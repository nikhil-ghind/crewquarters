from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.installation_create_in_approvedpermissions import (
        InstallationCreateInApprovedpermissions,
    )
    from ..models.installation_create_in_config import InstallationCreateInConfig
    from ..models.installation_create_in_modelbindings import InstallationCreateInModelbindings


T = TypeVar("T", bound="InstallationCreateIn")


@_attrs_define
class InstallationCreateIn:
    """
    Attributes:
        agent_id (str):
        approved_permissions (InstallationCreateInApprovedpermissions): Must equal the version's requested permissions
            exactly.
        version (None | str | Unset): Defaults to the catalog's current version.
        config (InstallationCreateInConfig | Unset):
        model_bindings (InstallationCreateInModelbindings | Unset): Owner choice of variant per requested profile
            family.
        enabled (bool | Unset):  Default: True.
    """

    agent_id: str
    approved_permissions: InstallationCreateInApprovedpermissions
    version: str | Unset | None = UNSET
    config: InstallationCreateInConfig | Unset = UNSET
    model_bindings: InstallationCreateInModelbindings | Unset = UNSET
    enabled: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        approved_permissions = self.approved_permissions.to_dict()

        version: str | Unset | None
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        model_bindings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_bindings, Unset):
            model_bindings = self.model_bindings.to_dict()

        enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agentId": agent_id,
                "approvedPermissions": approved_permissions,
            }
        )
        if version is not UNSET:
            field_dict["version"] = version
        if config is not UNSET:
            field_dict["config"] = config
        if model_bindings is not UNSET:
            field_dict["modelBindings"] = model_bindings
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.installation_create_in_approvedpermissions import (
            InstallationCreateInApprovedpermissions,
        )
        from ..models.installation_create_in_config import (
            InstallationCreateInConfig,
        )
        from ..models.installation_create_in_modelbindings import (
            InstallationCreateInModelbindings,
        )

        d = dict(src_dict)
        agent_id = d.pop("agentId")

        approved_permissions = InstallationCreateInApprovedpermissions.from_dict(
            d.pop("approvedPermissions")
        )

        def _parse_version(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        version = _parse_version(d.pop("version", UNSET))

        _config = d.pop("config", UNSET)
        config: InstallationCreateInConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = InstallationCreateInConfig.from_dict(_config)

        _model_bindings = d.pop("modelBindings", UNSET)
        model_bindings: InstallationCreateInModelbindings | Unset
        if isinstance(_model_bindings, Unset):
            model_bindings = UNSET
        else:
            model_bindings = InstallationCreateInModelbindings.from_dict(_model_bindings)

        enabled = d.pop("enabled", UNSET)

        installation_create_in = cls(
            agent_id=agent_id,
            approved_permissions=approved_permissions,
            version=version,
            config=config,
            model_bindings=model_bindings,
            enabled=enabled,
        )

        installation_create_in.additional_properties = d
        return installation_create_in

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
