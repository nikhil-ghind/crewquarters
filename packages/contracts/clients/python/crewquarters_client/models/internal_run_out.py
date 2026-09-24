from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.internal_run_out_state import InternalRunOutState

if TYPE_CHECKING:
    from ..models.internal_run_out_config import InternalRunOutConfig
    from ..models.internal_run_out_modelbindings import InternalRunOutModelbindings
    from ..models.internal_run_out_permissions import InternalRunOutPermissions


T = TypeVar("T", bound="InternalRunOut")


@_attrs_define
class InternalRunOut:
    """
    Attributes:
        id (UUID):
        state (InternalRunOutState):
        current_attempt (int):
        installation_id (UUID):
        cancel_requested (bool):
        capability_token_id (None | str): jti of the current attempt's capability token; reject any other.
        permissions (InternalRunOutPermissions):
        model_bindings (InternalRunOutModelbindings):
        config (InternalRunOutConfig):
    """

    id: UUID
    state: InternalRunOutState
    current_attempt: int
    installation_id: UUID
    cancel_requested: bool
    capability_token_id: str | None
    permissions: InternalRunOutPermissions
    model_bindings: InternalRunOutModelbindings
    config: InternalRunOutConfig
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        state = self.state.value

        current_attempt = self.current_attempt

        installation_id = str(self.installation_id)

        cancel_requested = self.cancel_requested

        capability_token_id: str | None
        capability_token_id = self.capability_token_id

        permissions = self.permissions.to_dict()

        model_bindings = self.model_bindings.to_dict()

        config = self.config.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "state": state,
                "currentAttempt": current_attempt,
                "installationId": installation_id,
                "cancelRequested": cancel_requested,
                "capabilityTokenId": capability_token_id,
                "permissions": permissions,
                "modelBindings": model_bindings,
                "config": config,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.internal_run_out_config import InternalRunOutConfig
        from ..models.internal_run_out_modelbindings import (
            InternalRunOutModelbindings,
        )
        from ..models.internal_run_out_permissions import InternalRunOutPermissions

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        state = InternalRunOutState(d.pop("state"))

        current_attempt = d.pop("currentAttempt")

        installation_id = UUID(d.pop("installationId"))

        cancel_requested = d.pop("cancelRequested")

        def _parse_capability_token_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        capability_token_id = _parse_capability_token_id(d.pop("capabilityTokenId"))

        permissions = InternalRunOutPermissions.from_dict(d.pop("permissions"))

        model_bindings = InternalRunOutModelbindings.from_dict(d.pop("modelBindings"))

        config = InternalRunOutConfig.from_dict(d.pop("config"))

        internal_run_out = cls(
            id=id,
            state=state,
            current_attempt=current_attempt,
            installation_id=installation_id,
            cancel_requested=cancel_requested,
            capability_token_id=capability_token_id,
            permissions=permissions,
            model_bindings=model_bindings,
            config=config,
        )

        internal_run_out.additional_properties = d
        return internal_run_out

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
