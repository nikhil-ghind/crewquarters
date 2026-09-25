from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.memory_out_models_item import MemoryOutModelsItem


T = TypeVar("T", bound="MemoryOut")


@_attrs_define
class MemoryOut:
    """Unified-memory breakdown for the top-bar resource popover (PLAN.md section 13.3).

    Attributes:
        total_bytes (int | None):
        available_bytes (int | None):
        system_reserve_bytes (int):
        max_serving_bytes (int):
        safety_margin_bytes (int):
        reserved_bytes (int):
        models (list[MemoryOutModelsItem]):
    """

    total_bytes: int | None
    available_bytes: int | None
    system_reserve_bytes: int
    max_serving_bytes: int
    safety_margin_bytes: int
    reserved_bytes: int
    models: list[MemoryOutModelsItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total_bytes: int | None
        total_bytes = self.total_bytes

        available_bytes: int | None
        available_bytes = self.available_bytes

        system_reserve_bytes = self.system_reserve_bytes

        max_serving_bytes = self.max_serving_bytes

        safety_margin_bytes = self.safety_margin_bytes

        reserved_bytes = self.reserved_bytes

        models = []
        for models_item_data in self.models:
            models_item = models_item_data.to_dict()
            models.append(models_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "totalBytes": total_bytes,
                "availableBytes": available_bytes,
                "systemReserveBytes": system_reserve_bytes,
                "maxServingBytes": max_serving_bytes,
                "safetyMarginBytes": safety_margin_bytes,
                "reservedBytes": reserved_bytes,
                "models": models,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.memory_out_models_item import MemoryOutModelsItem

        d = dict(src_dict)

        def _parse_total_bytes(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        total_bytes = _parse_total_bytes(d.pop("totalBytes"))

        def _parse_available_bytes(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        available_bytes = _parse_available_bytes(d.pop("availableBytes"))

        system_reserve_bytes = d.pop("systemReserveBytes")

        max_serving_bytes = d.pop("maxServingBytes")

        safety_margin_bytes = d.pop("safetyMarginBytes")

        reserved_bytes = d.pop("reservedBytes")

        models = []
        _models = d.pop("models")
        for models_item_data in _models:
            models_item = MemoryOutModelsItem.from_dict(models_item_data)

            models.append(models_item)

        memory_out = cls(
            total_bytes=total_bytes,
            available_bytes=available_bytes,
            system_reserve_bytes=system_reserve_bytes,
            max_serving_bytes=max_serving_bytes,
            safety_margin_bytes=safety_margin_bytes,
            reserved_bytes=reserved_bytes,
            models=models,
        )

        memory_out.additional_properties = d
        return memory_out

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
