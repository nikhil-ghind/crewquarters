from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_start_out_state import AgentStartOutState

T = TypeVar("T", bound="AgentStartOut")


@_attrs_define
class AgentStartOut:
    """
    Attributes:
        run_id (UUID):
        agent_id (str):
        installation_id (UUID):
        state (AgentStartOutState):
        created (bool): False when this startKey had already started the run.
    """

    run_id: UUID
    agent_id: str
    installation_id: UUID
    state: AgentStartOutState
    created: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        run_id = str(self.run_id)

        agent_id = self.agent_id

        installation_id = str(self.installation_id)

        state = self.state.value

        created = self.created

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "runId": run_id,
                "agentId": agent_id,
                "installationId": installation_id,
                "state": state,
                "created": created,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        run_id = UUID(d.pop("runId"))

        agent_id = d.pop("agentId")

        installation_id = UUID(d.pop("installationId"))

        state = AgentStartOutState(d.pop("state"))

        created = d.pop("created")

        agent_start_out = cls(
            run_id=run_id,
            agent_id=agent_id,
            installation_id=installation_id,
            state=state,
            created=created,
        )

        agent_start_out.additional_properties = d
        return agent_start_out

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
