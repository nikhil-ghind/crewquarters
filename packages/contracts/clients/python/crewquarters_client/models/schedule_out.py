from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.schedule_out_misfirepolicy import ScheduleOutMisfirepolicy

if TYPE_CHECKING:
    from ..models.occurrence_out import OccurrenceOut
    from ..models.readiness_check import ReadinessCheck


T = TypeVar("T", bound="ScheduleOut")


@_attrs_define
class ScheduleOut:
    """
    Attributes:
        id (UUID):
        installation_id (UUID):
        agent_name (str):
        ready (bool): Whether the agent's model, connections, and config are ready now.
        blockers (list[ReadinessCheck]): Readiness checks that are not ok.
        cron (str):
        timezone (str):
        misfire_policy (ScheduleOutMisfirepolicy):
        enabled (bool):
        next_run_at (datetime.datetime | None):
        next_occurrences (list[OccurrenceOut]):
        last_fired_at (datetime.datetime | None):
        last_run_id (None | UUID):
        version (int):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
    """

    id: UUID
    installation_id: UUID
    agent_name: str
    ready: bool
    blockers: list[ReadinessCheck]
    cron: str
    timezone: str
    misfire_policy: ScheduleOutMisfirepolicy
    enabled: bool
    next_run_at: datetime.datetime | None
    next_occurrences: list[OccurrenceOut]
    last_fired_at: datetime.datetime | None
    last_run_id: UUID | None
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        installation_id = str(self.installation_id)

        agent_name = self.agent_name

        ready = self.ready

        blockers = []
        for blockers_item_data in self.blockers:
            blockers_item = blockers_item_data.to_dict()
            blockers.append(blockers_item)

        cron = self.cron

        timezone = self.timezone

        misfire_policy = self.misfire_policy.value

        enabled = self.enabled

        next_run_at: str | None
        if isinstance(self.next_run_at, datetime.datetime):
            next_run_at = self.next_run_at.isoformat()
        else:
            next_run_at = self.next_run_at

        next_occurrences = []
        for next_occurrences_item_data in self.next_occurrences:
            next_occurrences_item = next_occurrences_item_data.to_dict()
            next_occurrences.append(next_occurrences_item)

        last_fired_at: str | None
        if isinstance(self.last_fired_at, datetime.datetime):
            last_fired_at = self.last_fired_at.isoformat()
        else:
            last_fired_at = self.last_fired_at

        last_run_id: str | None
        if isinstance(self.last_run_id, UUID):
            last_run_id = str(self.last_run_id)
        else:
            last_run_id = self.last_run_id

        version = self.version

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "installationId": installation_id,
                "agentName": agent_name,
                "ready": ready,
                "blockers": blockers,
                "cron": cron,
                "timezone": timezone,
                "misfirePolicy": misfire_policy,
                "enabled": enabled,
                "nextRunAt": next_run_at,
                "nextOccurrences": next_occurrences,
                "lastFiredAt": last_fired_at,
                "lastRunId": last_run_id,
                "version": version,
                "createdAt": created_at,
                "updatedAt": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.occurrence_out import OccurrenceOut
        from ..models.readiness_check import ReadinessCheck

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        installation_id = UUID(d.pop("installationId"))

        agent_name = d.pop("agentName")

        ready = d.pop("ready")

        blockers = []
        _blockers = d.pop("blockers")
        for blockers_item_data in _blockers:
            blockers_item = ReadinessCheck.from_dict(blockers_item_data)

            blockers.append(blockers_item)

        cron = d.pop("cron")

        timezone = d.pop("timezone")

        misfire_policy = ScheduleOutMisfirepolicy(d.pop("misfirePolicy"))

        enabled = d.pop("enabled")

        def _parse_next_run_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                next_run_at_type_0 = datetime.datetime.fromisoformat(data)

                return next_run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        next_run_at = _parse_next_run_at(d.pop("nextRunAt"))

        next_occurrences = []
        _next_occurrences = d.pop("nextOccurrences")
        for next_occurrences_item_data in _next_occurrences:
            next_occurrences_item = OccurrenceOut.from_dict(next_occurrences_item_data)

            next_occurrences.append(next_occurrences_item)

        def _parse_last_fired_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_fired_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_fired_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_fired_at = _parse_last_fired_at(d.pop("lastFiredAt"))

        def _parse_last_run_id(data: object) -> UUID | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_id_type_0 = UUID(data)

                return last_run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        last_run_id = _parse_last_run_id(d.pop("lastRunId"))

        version = d.pop("version")

        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updatedAt"))

        schedule_out = cls(
            id=id,
            installation_id=installation_id,
            agent_name=agent_name,
            ready=ready,
            blockers=blockers,
            cron=cron,
            timezone=timezone,
            misfire_policy=misfire_policy,
            enabled=enabled,
            next_run_at=next_run_at,
            next_occurrences=next_occurrences,
            last_fired_at=last_fired_at,
            last_run_id=last_run_id,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
        )

        schedule_out.additional_properties = d
        return schedule_out

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
