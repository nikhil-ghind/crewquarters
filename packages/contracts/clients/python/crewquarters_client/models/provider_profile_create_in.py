from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_profile_create_in_provider import ProviderProfileCreateInProvider
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.provider_profile_create_in_budgets import ProviderProfileCreateInBudgets


T = TypeVar("T", bound="ProviderProfileCreateIn")


@_attrs_define
class ProviderProfileCreateIn:
    """
    Attributes:
        provider (ProviderProfileCreateInProvider):
        display_name (str):
        api_key (str): Stored; never returned.
        allowed_models (list[str] | Unset):
        budgets (ProviderProfileCreateInBudgets | Unset):
        enabled (bool | Unset):  Default: True.
    """

    provider: ProviderProfileCreateInProvider
    display_name: str
    api_key: str
    allowed_models: list[str] | Unset = UNSET
    budgets: ProviderProfileCreateInBudgets | Unset = UNSET
    enabled: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        provider = self.provider.value

        display_name = self.display_name

        api_key = self.api_key

        allowed_models: list[str] | Unset = UNSET
        if not isinstance(self.allowed_models, Unset):
            allowed_models = self.allowed_models

        budgets: dict[str, Any] | Unset = UNSET
        if not isinstance(self.budgets, Unset):
            budgets = self.budgets.to_dict()

        enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "provider": provider,
                "displayName": display_name,
                "apiKey": api_key,
            }
        )
        if allowed_models is not UNSET:
            field_dict["allowedModels"] = allowed_models
        if budgets is not UNSET:
            field_dict["budgets"] = budgets
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_profile_create_in_budgets import (
            ProviderProfileCreateInBudgets,
        )

        d = dict(src_dict)
        provider = ProviderProfileCreateInProvider(d.pop("provider"))

        display_name = d.pop("displayName")

        api_key = d.pop("apiKey")

        allowed_models = cast(list[str], d.pop("allowedModels", UNSET))

        _budgets = d.pop("budgets", UNSET)
        budgets: ProviderProfileCreateInBudgets | Unset
        if isinstance(_budgets, Unset):
            budgets = UNSET
        else:
            budgets = ProviderProfileCreateInBudgets.from_dict(_budgets)

        enabled = d.pop("enabled", UNSET)

        provider_profile_create_in = cls(
            provider=provider,
            display_name=display_name,
            api_key=api_key,
            allowed_models=allowed_models,
            budgets=budgets,
            enabled=enabled,
        )

        provider_profile_create_in.additional_properties = d
        return provider_profile_create_in

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
