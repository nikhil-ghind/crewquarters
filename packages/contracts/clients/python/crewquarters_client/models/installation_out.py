from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.installation_out_approvedpermissions import InstallationOutApprovedpermissions
    from ..models.installation_out_config import InstallationOutConfig
    from ..models.installation_out_modelbindings import InstallationOutModelbindings
    from ..models.installation_out_requestedpermissions import InstallationOutRequestedpermissions
    from ..models.readiness import Readiness


T = TypeVar("T", bound="InstallationOut")


@_attrs_define
class InstallationOut:
    """
    Attributes:
        id (UUID):
        agent_id (str):
        agent_name (str):
        agent_version (str):
        agent_version_id (UUID):
        config (InstallationOutConfig):
        requested_permissions (InstallationOutRequestedpermissions):
        approved_permissions (InstallationOutApprovedpermissions):
        capabilities (list[str]):
        model_bindings (InstallationOutModelbindings):
        needs_reapproval (bool):
        enabled (bool):
        version (int):
        readiness (Readiness):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
    """

    id: UUID
    agent_id: str
    agent_name: str
    agent_version: str
    agent_version_id: UUID
    config: InstallationOutConfig
    requested_permissions: InstallationOutRequestedpermissions
    approved_permissions: InstallationOutApprovedpermissions
    capabilities: list[str]
    model_bindings: InstallationOutModelbindings
    needs_reapproval: bool
    enabled: bool
    version: int
    readiness: Readiness
    created_at: datetime.datetime
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        agent_id = self.agent_id

        agent_name = self.agent_name

        agent_version = self.agent_version

        agent_version_id = str(self.agent_version_id)

        config = self.config.to_dict()

        requested_permissions = self.requested_permissions.to_dict()

        approved_permissions = self.approved_permissions.to_dict()

        capabilities = self.capabilities

        model_bindings = self.model_bindings.to_dict()

        needs_reapproval = self.needs_reapproval

        enabled = self.enabled

        version = self.version

        readiness = self.readiness.to_dict()

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "agentId": agent_id,
                "agentName": agent_name,
                "agentVersion": agent_version,
                "agentVersionId": agent_version_id,
                "config": config,
                "requestedPermissions": requested_permissions,
                "approvedPermissions": approved_permissions,
                "capabilities": capabilities,
                "modelBindings": model_bindings,
                "needsReapproval": needs_reapproval,
                "enabled": enabled,
                "version": version,
                "readiness": readiness,
                "createdAt": created_at,
                "updatedAt": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.installation_out_approvedpermissions import (
            InstallationOutApprovedpermissions,
        )
        from ..models.installation_out_config import InstallationOutConfig
        from ..models.installation_out_modelbindings import (
            InstallationOutModelbindings,
        )
        from ..models.installation_out_requestedpermissions import (
            InstallationOutRequestedpermissions,
        )
        from ..models.readiness import Readiness

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        agent_id = d.pop("agentId")

        agent_name = d.pop("agentName")

        agent_version = d.pop("agentVersion")

        agent_version_id = UUID(d.pop("agentVersionId"))

        config = InstallationOutConfig.from_dict(d.pop("config"))

        requested_permissions = InstallationOutRequestedpermissions.from_dict(
            d.pop("requestedPermissions")
        )

        approved_permissions = InstallationOutApprovedpermissions.from_dict(
            d.pop("approvedPermissions")
        )

        capabilities = cast(list[str], d.pop("capabilities"))

        model_bindings = InstallationOutModelbindings.from_dict(d.pop("modelBindings"))

        needs_reapproval = d.pop("needsReapproval")

        enabled = d.pop("enabled")

        version = d.pop("version")

        readiness = Readiness.from_dict(d.pop("readiness"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updatedAt"))

        installation_out = cls(
            id=id,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_version=agent_version,
            agent_version_id=agent_version_id,
            config=config,
            requested_permissions=requested_permissions,
            approved_permissions=approved_permissions,
            capabilities=capabilities,
            model_bindings=model_bindings,
            needs_reapproval=needs_reapproval,
            enabled=enabled,
            version=version,
            readiness=readiness,
            created_at=created_at,
            updated_at=updated_at,
        )

        installation_out.additional_properties = d
        return installation_out

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
