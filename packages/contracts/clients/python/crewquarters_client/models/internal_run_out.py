from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.internal_run_out_state import InternalRunOutState
from ..models.internal_run_out_trigger import InternalRunOutTrigger
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.internal_run_out_config import InternalRunOutConfig
    from ..models.internal_run_out_modelbindings import InternalRunOutModelbindings
    from ..models.internal_run_out_permissions import InternalRunOutPermissions
    from ..models.internal_run_out_trigger_input_type_0 import InternalRunOutTriggerInputType0


T = TypeVar("T", bound="InternalRunOut")


@_attrs_define
class InternalRunOut:
    """
    Attributes:
        id (UUID):
        state (InternalRunOutState):
        current_attempt (int):
        installation_id (UUID):
        trigger (InternalRunOutTrigger):
        scheduled_for (datetime.datetime | None):
        agent_id (str): Manifest agent id.
        agent_version (str): Manifest version (semver).
        agent_version_id (UUID):
        created_at (datetime.datetime):
        active_timeout_seconds (int): Active-time limit per attempt.
        active_seconds_remaining (float):
        max_input_wait_seconds (int):
        input_wait_remaining_seconds (float): Remaining input-wait budget.
        cancel_requested (bool):
        capability_token_id (None | str): jti of the current attempt's capability token; reject any other.
        permissions (InternalRunOutPermissions):
        model_bindings (InternalRunOutModelbindings):
        config (InternalRunOutConfig):
        parent_run_id (None | Unset | UUID):
        trigger_input (InternalRunOutTriggerInputType0 | None | Unset): Untrusted input the starting agent passed
            (trigger `agent`).
    """

    id: UUID
    state: InternalRunOutState
    current_attempt: int
    installation_id: UUID
    trigger: InternalRunOutTrigger
    scheduled_for: datetime.datetime | None
    agent_id: str
    agent_version: str
    agent_version_id: UUID
    created_at: datetime.datetime
    active_timeout_seconds: int
    active_seconds_remaining: float
    max_input_wait_seconds: int
    input_wait_remaining_seconds: float
    cancel_requested: bool
    capability_token_id: str | None
    permissions: InternalRunOutPermissions
    model_bindings: InternalRunOutModelbindings
    config: InternalRunOutConfig
    parent_run_id: Unset | UUID | None = UNSET
    trigger_input: InternalRunOutTriggerInputType0 | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.internal_run_out_trigger_input_type_0 import InternalRunOutTriggerInputType0

        id = str(self.id)

        state = self.state.value

        current_attempt = self.current_attempt

        installation_id = str(self.installation_id)

        trigger = self.trigger.value

        scheduled_for: str | None
        if isinstance(self.scheduled_for, datetime.datetime):
            scheduled_for = self.scheduled_for.isoformat()
        else:
            scheduled_for = self.scheduled_for

        agent_id = self.agent_id

        agent_version = self.agent_version

        agent_version_id = str(self.agent_version_id)

        created_at = self.created_at.isoformat()

        active_timeout_seconds = self.active_timeout_seconds

        active_seconds_remaining = self.active_seconds_remaining

        max_input_wait_seconds = self.max_input_wait_seconds

        input_wait_remaining_seconds = self.input_wait_remaining_seconds

        cancel_requested = self.cancel_requested

        capability_token_id: str | None
        capability_token_id = self.capability_token_id

        permissions = self.permissions.to_dict()

        model_bindings = self.model_bindings.to_dict()

        config = self.config.to_dict()

        parent_run_id: str | Unset | None
        if isinstance(self.parent_run_id, Unset):
            parent_run_id = UNSET
        elif isinstance(self.parent_run_id, UUID):
            parent_run_id = str(self.parent_run_id)
        else:
            parent_run_id = self.parent_run_id

        trigger_input: dict[str, Any] | Unset | None
        if isinstance(self.trigger_input, Unset):
            trigger_input = UNSET
        elif isinstance(self.trigger_input, InternalRunOutTriggerInputType0):
            trigger_input = self.trigger_input.to_dict()
        else:
            trigger_input = self.trigger_input

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "state": state,
                "currentAttempt": current_attempt,
                "installationId": installation_id,
                "trigger": trigger,
                "scheduledFor": scheduled_for,
                "agentId": agent_id,
                "agentVersion": agent_version,
                "agentVersionId": agent_version_id,
                "createdAt": created_at,
                "activeTimeoutSeconds": active_timeout_seconds,
                "activeSecondsRemaining": active_seconds_remaining,
                "maxInputWaitSeconds": max_input_wait_seconds,
                "inputWaitRemainingSeconds": input_wait_remaining_seconds,
                "cancelRequested": cancel_requested,
                "capabilityTokenId": capability_token_id,
                "permissions": permissions,
                "modelBindings": model_bindings,
                "config": config,
            }
        )
        if parent_run_id is not UNSET:
            field_dict["parentRunId"] = parent_run_id
        if trigger_input is not UNSET:
            field_dict["triggerInput"] = trigger_input

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.internal_run_out_config import InternalRunOutConfig
        from ..models.internal_run_out_modelbindings import (
            InternalRunOutModelbindings,
        )
        from ..models.internal_run_out_permissions import InternalRunOutPermissions
        from ..models.internal_run_out_trigger_input_type_0 import (
            InternalRunOutTriggerInputType0,
        )

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        state = InternalRunOutState(d.pop("state"))

        current_attempt = d.pop("currentAttempt")

        installation_id = UUID(d.pop("installationId"))

        trigger = InternalRunOutTrigger(d.pop("trigger"))

        def _parse_scheduled_for(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                scheduled_for_type_0 = datetime.datetime.fromisoformat(data)

                return scheduled_for_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        scheduled_for = _parse_scheduled_for(d.pop("scheduledFor"))

        agent_id = d.pop("agentId")

        agent_version = d.pop("agentVersion")

        agent_version_id = UUID(d.pop("agentVersionId"))

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        active_timeout_seconds = d.pop("activeTimeoutSeconds")

        active_seconds_remaining = d.pop("activeSecondsRemaining")

        max_input_wait_seconds = d.pop("maxInputWaitSeconds")

        input_wait_remaining_seconds = d.pop("inputWaitRemainingSeconds")

        cancel_requested = d.pop("cancelRequested")

        def _parse_capability_token_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        capability_token_id = _parse_capability_token_id(d.pop("capabilityTokenId"))

        permissions = InternalRunOutPermissions.from_dict(d.pop("permissions"))

        model_bindings = InternalRunOutModelbindings.from_dict(d.pop("modelBindings"))

        config = InternalRunOutConfig.from_dict(d.pop("config"))

        def _parse_parent_run_id(data: object) -> Unset | UUID | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                parent_run_id_type_0 = UUID(data)

                return parent_run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        parent_run_id = _parse_parent_run_id(d.pop("parentRunId", UNSET))

        def _parse_trigger_input(data: object) -> InternalRunOutTriggerInputType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                trigger_input_type_0 = InternalRunOutTriggerInputType0.from_dict(data)

                return trigger_input_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InternalRunOutTriggerInputType0 | None | Unset, data)

        trigger_input = _parse_trigger_input(d.pop("triggerInput", UNSET))

        internal_run_out = cls(
            id=id,
            state=state,
            current_attempt=current_attempt,
            installation_id=installation_id,
            trigger=trigger,
            scheduled_for=scheduled_for,
            agent_id=agent_id,
            agent_version=agent_version,
            agent_version_id=agent_version_id,
            created_at=created_at,
            active_timeout_seconds=active_timeout_seconds,
            active_seconds_remaining=active_seconds_remaining,
            max_input_wait_seconds=max_input_wait_seconds,
            input_wait_remaining_seconds=input_wait_remaining_seconds,
            cancel_requested=cancel_requested,
            capability_token_id=capability_token_id,
            permissions=permissions,
            model_bindings=model_bindings,
            config=config,
            parent_run_id=parent_run_id,
            trigger_input=trigger_input,
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
