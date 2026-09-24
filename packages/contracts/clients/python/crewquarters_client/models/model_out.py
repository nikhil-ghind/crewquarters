from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_out_active_leases_item import ModelOutActiveLeasesItem
    from ..models.model_out_license_type_0 import ModelOutLicenseType0


T = TypeVar("T", bound="ModelOut")


@_attrs_define
class ModelOut:
    """
    Attributes:
        id (str):
        display_name (str):
        family (str):
        download_state (str):
        memory_state (str):
        disk_bytes (int | None | Unset):
        expected_memory_bytes (int | None | Unset):
        context_limit (int | None | Unset):
        capabilities (list[str] | Unset):
        validation (None | str | Unset):
        license_ (ModelOutLicenseType0 | None | Unset):
        active_leases (list[ModelOutActiveLeasesItem] | Unset):
    """

    id: str
    display_name: str
    family: str
    download_state: str
    memory_state: str
    disk_bytes: int | Unset | None = UNSET
    expected_memory_bytes: int | Unset | None = UNSET
    context_limit: int | Unset | None = UNSET
    capabilities: list[str] | Unset = UNSET
    validation: str | Unset | None = UNSET
    license_: ModelOutLicenseType0 | Unset | None = UNSET
    active_leases: list[ModelOutActiveLeasesItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_out_license_type_0 import ModelOutLicenseType0

        id = self.id

        display_name = self.display_name

        family = self.family

        download_state = self.download_state

        memory_state = self.memory_state

        disk_bytes: int | Unset | None
        if isinstance(self.disk_bytes, Unset):
            disk_bytes = UNSET
        else:
            disk_bytes = self.disk_bytes

        expected_memory_bytes: int | Unset | None
        if isinstance(self.expected_memory_bytes, Unset):
            expected_memory_bytes = UNSET
        else:
            expected_memory_bytes = self.expected_memory_bytes

        context_limit: int | Unset | None
        if isinstance(self.context_limit, Unset):
            context_limit = UNSET
        else:
            context_limit = self.context_limit

        capabilities: list[str] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = self.capabilities

        validation: str | Unset | None
        if isinstance(self.validation, Unset):
            validation = UNSET
        else:
            validation = self.validation

        license_: dict[str, Any] | Unset | None
        if isinstance(self.license_, Unset):
            license_ = UNSET
        elif isinstance(self.license_, ModelOutLicenseType0):
            license_ = self.license_.to_dict()
        else:
            license_ = self.license_

        active_leases: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.active_leases, Unset):
            active_leases = []
            for active_leases_item_data in self.active_leases:
                active_leases_item = active_leases_item_data.to_dict()
                active_leases.append(active_leases_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "displayName": display_name,
                "family": family,
                "downloadState": download_state,
                "memoryState": memory_state,
            }
        )
        if disk_bytes is not UNSET:
            field_dict["diskBytes"] = disk_bytes
        if expected_memory_bytes is not UNSET:
            field_dict["expectedMemoryBytes"] = expected_memory_bytes
        if context_limit is not UNSET:
            field_dict["contextLimit"] = context_limit
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if validation is not UNSET:
            field_dict["validation"] = validation
        if license_ is not UNSET:
            field_dict["license"] = license_
        if active_leases is not UNSET:
            field_dict["activeLeases"] = active_leases

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_out_active_leases_item import ModelOutActiveLeasesItem
        from ..models.model_out_license_type_0 import ModelOutLicenseType0

        d = dict(src_dict)
        id = d.pop("id")

        display_name = d.pop("displayName")

        family = d.pop("family")

        download_state = d.pop("downloadState")

        memory_state = d.pop("memoryState")

        def _parse_disk_bytes(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        disk_bytes = _parse_disk_bytes(d.pop("diskBytes", UNSET))

        def _parse_expected_memory_bytes(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_memory_bytes = _parse_expected_memory_bytes(d.pop("expectedMemoryBytes", UNSET))

        def _parse_context_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        context_limit = _parse_context_limit(d.pop("contextLimit", UNSET))

        capabilities = cast(list[str], d.pop("capabilities", UNSET))

        def _parse_validation(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        validation = _parse_validation(d.pop("validation", UNSET))

        def _parse_license_(data: object) -> ModelOutLicenseType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                license_type_0 = ModelOutLicenseType0.from_dict(data)

                return license_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelOutLicenseType0 | None | Unset, data)

        license_ = _parse_license_(d.pop("license", UNSET))

        _active_leases = d.pop("activeLeases", UNSET)
        active_leases: list[ModelOutActiveLeasesItem] | Unset = UNSET
        if _active_leases is not UNSET:
            active_leases = []
            for active_leases_item_data in _active_leases:
                active_leases_item = ModelOutActiveLeasesItem.from_dict(active_leases_item_data)

                active_leases.append(active_leases_item)

        model_out = cls(
            id=id,
            display_name=display_name,
            family=family,
            download_state=download_state,
            memory_state=memory_state,
            disk_bytes=disk_bytes,
            expected_memory_bytes=expected_memory_bytes,
            context_limit=context_limit,
            capabilities=capabilities,
            validation=validation,
            license_=license_,
            active_leases=active_leases,
        )

        model_out.additional_properties = d
        return model_out

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
