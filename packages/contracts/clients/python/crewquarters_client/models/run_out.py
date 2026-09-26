from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.run_out_state import RunOutState
from ..models.run_out_trigger import RunOutTrigger
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_out_error_type_0 import RunOutErrorType0
    from ..models.run_out_result_type_0 import RunOutResultType0


T = TypeVar("T", bound="RunOut")


@_attrs_define
class RunOut:
    """
    Attributes:
        id (UUID):
        installation_id (UUID):
        agent_id (str):
        agent_name (str):
        agent_version (str):
        trigger (RunOutTrigger):
        schedule_id (None | UUID):
        scheduled_for (datetime.datetime | None):
        state (RunOutState):
        current_attempt (int):
        result (None | RunOutResultType0):
        error (None | RunOutErrorType0):
        retryable (bool):
        cancel_requested (bool):
        acknowledged_at (datetime.datetime | None):
        active_seconds_used (float):
        input_wait_seconds_used (float):
        active_timeout_seconds (int):
        max_input_wait_seconds (int):
        uses_cloud (bool):
        pending_input_count (int):
        created_at (datetime.datetime):
        started_at (datetime.datetime | None):
        finished_at (datetime.datetime | None):
        updated_at (datetime.datetime):
        parent_run_id (None | Unset | UUID): The run that started this one (trigger `agent`).
    """

    id: UUID
    installation_id: UUID
    agent_id: str
    agent_name: str
    agent_version: str
    trigger: RunOutTrigger
    schedule_id: UUID | None
    scheduled_for: datetime.datetime | None
    state: RunOutState
    current_attempt: int
    result: RunOutResultType0 | None
    error: RunOutErrorType0 | None
    retryable: bool
    cancel_requested: bool
    acknowledged_at: datetime.datetime | None
    active_seconds_used: float
    input_wait_seconds_used: float
    active_timeout_seconds: int
    max_input_wait_seconds: int
    uses_cloud: bool
    pending_input_count: int
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    updated_at: datetime.datetime
    parent_run_id: Unset | UUID | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.run_out_error_type_0 import RunOutErrorType0
        from ..models.run_out_result_type_0 import RunOutResultType0

        id = str(self.id)

        installation_id = str(self.installation_id)

        agent_id = self.agent_id

        agent_name = self.agent_name

        agent_version = self.agent_version

        trigger = self.trigger.value

        schedule_id: str | None
        if isinstance(self.schedule_id, UUID):
            schedule_id = str(self.schedule_id)
        else:
            schedule_id = self.schedule_id

        scheduled_for: str | None
        if isinstance(self.scheduled_for, datetime.datetime):
            scheduled_for = self.scheduled_for.isoformat()
        else:
            scheduled_for = self.scheduled_for

        state = self.state.value

        current_attempt = self.current_attempt

        result: dict[str, Any] | None
        if isinstance(self.result, RunOutResultType0):
            result = self.result.to_dict()
        else:
            result = self.result

        error: dict[str, Any] | None
        if isinstance(self.error, RunOutErrorType0):
            error = self.error.to_dict()
        else:
            error = self.error

        retryable = self.retryable

        cancel_requested = self.cancel_requested

        acknowledged_at: str | None
        if isinstance(self.acknowledged_at, datetime.datetime):
            acknowledged_at = self.acknowledged_at.isoformat()
        else:
            acknowledged_at = self.acknowledged_at

        active_seconds_used = self.active_seconds_used

        input_wait_seconds_used = self.input_wait_seconds_used

        active_timeout_seconds = self.active_timeout_seconds

        max_input_wait_seconds = self.max_input_wait_seconds

        uses_cloud = self.uses_cloud

        pending_input_count = self.pending_input_count

        created_at = self.created_at.isoformat()

        started_at: str | None
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        finished_at: str | None
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        updated_at = self.updated_at.isoformat()

        parent_run_id: str | Unset | None
        if isinstance(self.parent_run_id, Unset):
            parent_run_id = UNSET
        elif isinstance(self.parent_run_id, UUID):
            parent_run_id = str(self.parent_run_id)
        else:
            parent_run_id = self.parent_run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "installationId": installation_id,
                "agentId": agent_id,
                "agentName": agent_name,
                "agentVersion": agent_version,
                "trigger": trigger,
                "scheduleId": schedule_id,
                "scheduledFor": scheduled_for,
                "state": state,
                "currentAttempt": current_attempt,
                "result": result,
                "error": error,
                "retryable": retryable,
                "cancelRequested": cancel_requested,
                "acknowledgedAt": acknowledged_at,
                "activeSecondsUsed": active_seconds_used,
                "inputWaitSecondsUsed": input_wait_seconds_used,
                "activeTimeoutSeconds": active_timeout_seconds,
                "maxInputWaitSeconds": max_input_wait_seconds,
                "usesCloud": uses_cloud,
                "pendingInputCount": pending_input_count,
                "createdAt": created_at,
                "startedAt": started_at,
                "finishedAt": finished_at,
                "updatedAt": updated_at,
            }
        )
        if parent_run_id is not UNSET:
            field_dict["parentRunId"] = parent_run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_out_error_type_0 import RunOutErrorType0
        from ..models.run_out_result_type_0 import RunOutResultType0

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        installation_id = UUID(d.pop("installationId"))

        agent_id = d.pop("agentId")

        agent_name = d.pop("agentName")

        agent_version = d.pop("agentVersion")

        trigger = RunOutTrigger(d.pop("trigger"))

        def _parse_schedule_id(data: object) -> UUID | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                schedule_id_type_0 = UUID(data)

                return schedule_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        schedule_id = _parse_schedule_id(d.pop("scheduleId"))

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

        state = RunOutState(d.pop("state"))

        current_attempt = d.pop("currentAttempt")

        def _parse_result(data: object) -> RunOutResultType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                result_type_0 = RunOutResultType0.from_dict(data)

                return result_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunOutResultType0, data)

        result = _parse_result(d.pop("result"))

        def _parse_error(data: object) -> RunOutErrorType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                error_type_0 = RunOutErrorType0.from_dict(data)

                return error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunOutErrorType0, data)

        error = _parse_error(d.pop("error"))

        retryable = d.pop("retryable")

        cancel_requested = d.pop("cancelRequested")

        def _parse_acknowledged_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                acknowledged_at_type_0 = datetime.datetime.fromisoformat(data)

                return acknowledged_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        acknowledged_at = _parse_acknowledged_at(d.pop("acknowledgedAt"))

        active_seconds_used = d.pop("activeSecondsUsed")

        input_wait_seconds_used = d.pop("inputWaitSecondsUsed")

        active_timeout_seconds = d.pop("activeTimeoutSeconds")

        max_input_wait_seconds = d.pop("maxInputWaitSeconds")

        uses_cloud = d.pop("usesCloud")

        pending_input_count = d.pop("pendingInputCount")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("startedAt"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finishedAt"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updatedAt"))

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

        run_out = cls(
            id=id,
            installation_id=installation_id,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_version=agent_version,
            trigger=trigger,
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            state=state,
            current_attempt=current_attempt,
            result=result,
            error=error,
            retryable=retryable,
            cancel_requested=cancel_requested,
            acknowledged_at=acknowledged_at,
            active_seconds_used=active_seconds_used,
            input_wait_seconds_used=input_wait_seconds_used,
            active_timeout_seconds=active_timeout_seconds,
            max_input_wait_seconds=max_input_wait_seconds,
            uses_cloud=uses_cloud,
            pending_input_count=pending_input_count,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            updated_at=updated_at,
            parent_run_id=parent_run_id,
        )

        run_out.additional_properties = d
        return run_out

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
