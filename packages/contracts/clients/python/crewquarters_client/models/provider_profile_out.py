from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_profile_out_provider import ProviderProfileOutProvider

if TYPE_CHECKING:
    from ..models.provider_profile_out_budgets import ProviderProfileOutBudgets


T = TypeVar("T", bound="ProviderProfileOut")


@_attrs_define
class ProviderProfileOut:
    """
    Attributes:
        id (UUID):
        provider (ProviderProfileOutProvider):
        display_name (str):
        allowed_models (list[str]):
        budgets (ProviderProfileOutBudgets):
        enabled (bool):
        status (str):
        last_checked_at (datetime.datetime | None):
    """

    id: UUID
    provider: ProviderProfileOutProvider
    display_name: str
    allowed_models: list[str]
    budgets: ProviderProfileOutBudgets
    enabled: bool
    status: str
    last_checked_at: datetime.datetime | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        provider = self.provider.value

        display_name = self.display_name

        allowed_models = self.allowed_models

        budgets = self.budgets.to_dict()

        enabled = self.enabled

        status = self.status

        last_checked_at: str | None
        if isinstance(self.last_checked_at, datetime.datetime):
            last_checked_at = self.last_checked_at.isoformat()
        else:
            last_checked_at = self.last_checked_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "provider": provider,
                "displayName": display_name,
                "allowedModels": allowed_models,
                "budgets": budgets,
                "enabled": enabled,
                "status": status,
                "lastCheckedAt": last_checked_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_profile_out_budgets import ProviderProfileOutBudgets

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        provider = ProviderProfileOutProvider(d.pop("provider"))

        display_name = d.pop("displayName")

        allowed_models = cast(list[str], d.pop("allowedModels"))

        budgets = ProviderProfileOutBudgets.from_dict(d.pop("budgets"))

        enabled = d.pop("enabled")

        status = d.pop("status")

        def _parse_last_checked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_checked_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_checked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_checked_at = _parse_last_checked_at(d.pop("lastCheckedAt"))

        provider_profile_out = cls(
            id=id,
            provider=provider,
            display_name=display_name,
            allowed_models=allowed_models,
            budgets=budgets,
            enabled=enabled,
            status=status,
            last_checked_at=last_checked_at,
        )

        provider_profile_out.additional_properties = d
        return provider_profile_out

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
