from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_version_out_configurationschema import AgentVersionOutConfigurationschema
    from ..models.agent_version_out_permissions import AgentVersionOutPermissions
    from ..models.agent_version_out_resources import AgentVersionOutResources
    from ..models.agent_version_out_result_schema_type_0 import AgentVersionOutResultSchemaType0


T = TypeVar("T", bound="AgentVersionOut")


@_attrs_define
class AgentVersionOut:
    """
    Attributes:
        id (UUID):
        version (str):
        image (str):
        image_digest (str):
        sdk_protocol (str):
        architectures (list[str]):
        triggers (list[str]):
        permissions (AgentVersionOutPermissions):
        resources (AgentVersionOutResources):
        configuration_schema (AgentVersionOutConfigurationschema):
        result_schema (AgentVersionOutResultSchemaType0 | None):
        compatible (bool):
        compatibility_issues (list[str]):
        created_at (datetime.datetime):
    """

    id: UUID
    version: str
    image: str
    image_digest: str
    sdk_protocol: str
    architectures: list[str]
    triggers: list[str]
    permissions: AgentVersionOutPermissions
    resources: AgentVersionOutResources
    configuration_schema: AgentVersionOutConfigurationschema
    result_schema: AgentVersionOutResultSchemaType0 | None
    compatible: bool
    compatibility_issues: list[str]
    created_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_version_out_result_schema_type_0 import AgentVersionOutResultSchemaType0

        id = str(self.id)

        version = self.version

        image = self.image

        image_digest = self.image_digest

        sdk_protocol = self.sdk_protocol

        architectures = self.architectures

        triggers = self.triggers

        permissions = self.permissions.to_dict()

        resources = self.resources.to_dict()

        configuration_schema = self.configuration_schema.to_dict()

        result_schema: dict[str, Any] | None
        if isinstance(self.result_schema, AgentVersionOutResultSchemaType0):
            result_schema = self.result_schema.to_dict()
        else:
            result_schema = self.result_schema

        compatible = self.compatible

        compatibility_issues = self.compatibility_issues

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "version": version,
                "image": image,
                "imageDigest": image_digest,
                "sdkProtocol": sdk_protocol,
                "architectures": architectures,
                "triggers": triggers,
                "permissions": permissions,
                "resources": resources,
                "configurationSchema": configuration_schema,
                "resultSchema": result_schema,
                "compatible": compatible,
                "compatibilityIssues": compatibility_issues,
                "createdAt": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_version_out_configurationschema import (
            AgentVersionOutConfigurationschema,
        )
        from ..models.agent_version_out_permissions import (
            AgentVersionOutPermissions,
        )
        from ..models.agent_version_out_resources import AgentVersionOutResources
        from ..models.agent_version_out_result_schema_type_0 import (
            AgentVersionOutResultSchemaType0,
        )

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        version = d.pop("version")

        image = d.pop("image")

        image_digest = d.pop("imageDigest")

        sdk_protocol = d.pop("sdkProtocol")

        architectures = cast(list[str], d.pop("architectures"))

        triggers = cast(list[str], d.pop("triggers"))

        permissions = AgentVersionOutPermissions.from_dict(d.pop("permissions"))

        resources = AgentVersionOutResources.from_dict(d.pop("resources"))

        configuration_schema = AgentVersionOutConfigurationschema.from_dict(
            d.pop("configurationSchema")
        )

        def _parse_result_schema(data: object) -> AgentVersionOutResultSchemaType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                result_schema_type_0 = AgentVersionOutResultSchemaType0.from_dict(data)

                return result_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentVersionOutResultSchemaType0 | None, data)

        result_schema = _parse_result_schema(d.pop("resultSchema"))

        compatible = d.pop("compatible")

        compatibility_issues = cast(list[str], d.pop("compatibilityIssues"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        agent_version_out = cls(
            id=id,
            version=version,
            image=image,
            image_digest=image_digest,
            sdk_protocol=sdk_protocol,
            architectures=architectures,
            triggers=triggers,
            permissions=permissions,
            resources=resources,
            configuration_schema=configuration_schema,
            result_schema=result_schema,
            compatible=compatible,
            compatibility_issues=compatibility_issues,
            created_at=created_at,
        )

        agent_version_out.additional_properties = d
        return agent_version_out

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
