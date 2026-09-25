from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_out_downloadstate import ModelOutDownloadstate
from ..models.model_out_memorystate import ModelOutMemorystate
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_download_out import ModelDownloadOut
    from ..models.model_lease_out import ModelLeaseOut
    from ..models.model_out_error_type_0 import ModelOutErrorType0
    from ..models.model_out_license_type_0 import ModelOutLicenseType0


T = TypeVar("T", bound="ModelOut")


@_attrs_define
class ModelOut:
    """Installed-on-disk and loaded-in-memory are separate fields (PLAN.md section 13.8).

    Attributes:
        id (str):
        display_name (str):
        family (str):
        download_state (ModelOutDownloadstate):
        memory_state (ModelOutMemorystate):
        backend (str | Unset):  Default: 'vllm'.
        stage (None | str | Unset): Current load stage, e.g. 'Loading weights'.
        disk_bytes (int | None | Unset):
        download (ModelDownloadOut | Unset):
        expected_memory_bytes (int | None | Unset):
        reserved_bytes (int | Unset):  Default: 0.
        context_limit (int | None | Unset):
        capabilities (list[str] | Unset):
        validation (None | str | Unset):
        license_ (ModelOutLicenseType0 | None | Unset):
        error (ModelOutErrorType0 | None | Unset):
        load_started_at (datetime.datetime | None | Unset):
        ready_at (datetime.datetime | None | Unset):
        idle_unload_at (datetime.datetime | None | Unset):
        active_leases (list[ModelLeaseOut] | Unset):
    """

    id: str
    display_name: str
    family: str
    download_state: ModelOutDownloadstate
    memory_state: ModelOutMemorystate
    backend: str | Unset = "vllm"
    stage: str | Unset | None = UNSET
    disk_bytes: int | Unset | None = UNSET
    download: ModelDownloadOut | Unset = UNSET
    expected_memory_bytes: int | Unset | None = UNSET
    reserved_bytes: int | Unset = 0
    context_limit: int | Unset | None = UNSET
    capabilities: list[str] | Unset = UNSET
    validation: str | Unset | None = UNSET
    license_: ModelOutLicenseType0 | Unset | None = UNSET
    error: ModelOutErrorType0 | Unset | None = UNSET
    load_started_at: datetime.datetime | Unset | None = UNSET
    ready_at: datetime.datetime | Unset | None = UNSET
    idle_unload_at: datetime.datetime | Unset | None = UNSET
    active_leases: list[ModelLeaseOut] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_out_error_type_0 import ModelOutErrorType0
        from ..models.model_out_license_type_0 import ModelOutLicenseType0

        id = self.id

        display_name = self.display_name

        family = self.family

        download_state = self.download_state.value

        memory_state = self.memory_state.value

        backend = self.backend

        stage: str | Unset | None
        if isinstance(self.stage, Unset):
            stage = UNSET
        else:
            stage = self.stage

        disk_bytes: int | Unset | None
        if isinstance(self.disk_bytes, Unset):
            disk_bytes = UNSET
        else:
            disk_bytes = self.disk_bytes

        download: dict[str, Any] | Unset = UNSET
        if not isinstance(self.download, Unset):
            download = self.download.to_dict()

        expected_memory_bytes: int | Unset | None
        if isinstance(self.expected_memory_bytes, Unset):
            expected_memory_bytes = UNSET
        else:
            expected_memory_bytes = self.expected_memory_bytes

        reserved_bytes = self.reserved_bytes

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

        error: dict[str, Any] | Unset | None
        if isinstance(self.error, Unset):
            error = UNSET
        elif isinstance(self.error, ModelOutErrorType0):
            error = self.error.to_dict()
        else:
            error = self.error

        load_started_at: str | Unset | None
        if isinstance(self.load_started_at, Unset):
            load_started_at = UNSET
        elif isinstance(self.load_started_at, datetime.datetime):
            load_started_at = self.load_started_at.isoformat()
        else:
            load_started_at = self.load_started_at

        ready_at: str | Unset | None
        if isinstance(self.ready_at, Unset):
            ready_at = UNSET
        elif isinstance(self.ready_at, datetime.datetime):
            ready_at = self.ready_at.isoformat()
        else:
            ready_at = self.ready_at

        idle_unload_at: str | Unset | None
        if isinstance(self.idle_unload_at, Unset):
            idle_unload_at = UNSET
        elif isinstance(self.idle_unload_at, datetime.datetime):
            idle_unload_at = self.idle_unload_at.isoformat()
        else:
            idle_unload_at = self.idle_unload_at

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
        if backend is not UNSET:
            field_dict["backend"] = backend
        if stage is not UNSET:
            field_dict["stage"] = stage
        if disk_bytes is not UNSET:
            field_dict["diskBytes"] = disk_bytes
        if download is not UNSET:
            field_dict["download"] = download
        if expected_memory_bytes is not UNSET:
            field_dict["expectedMemoryBytes"] = expected_memory_bytes
        if reserved_bytes is not UNSET:
            field_dict["reservedBytes"] = reserved_bytes
        if context_limit is not UNSET:
            field_dict["contextLimit"] = context_limit
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if validation is not UNSET:
            field_dict["validation"] = validation
        if license_ is not UNSET:
            field_dict["license"] = license_
        if error is not UNSET:
            field_dict["error"] = error
        if load_started_at is not UNSET:
            field_dict["loadStartedAt"] = load_started_at
        if ready_at is not UNSET:
            field_dict["readyAt"] = ready_at
        if idle_unload_at is not UNSET:
            field_dict["idleUnloadAt"] = idle_unload_at
        if active_leases is not UNSET:
            field_dict["activeLeases"] = active_leases

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_download_out import ModelDownloadOut
        from ..models.model_lease_out import ModelLeaseOut
        from ..models.model_out_error_type_0 import ModelOutErrorType0
        from ..models.model_out_license_type_0 import ModelOutLicenseType0

        d = dict(src_dict)
        id = d.pop("id")

        display_name = d.pop("displayName")

        family = d.pop("family")

        download_state = ModelOutDownloadstate(d.pop("downloadState"))

        memory_state = ModelOutMemorystate(d.pop("memoryState"))

        backend = d.pop("backend", UNSET)

        def _parse_stage(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        stage = _parse_stage(d.pop("stage", UNSET))

        def _parse_disk_bytes(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        disk_bytes = _parse_disk_bytes(d.pop("diskBytes", UNSET))

        _download = d.pop("download", UNSET)
        download: ModelDownloadOut | Unset
        if isinstance(_download, Unset):
            download = UNSET
        else:
            download = ModelDownloadOut.from_dict(_download)

        def _parse_expected_memory_bytes(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_memory_bytes = _parse_expected_memory_bytes(d.pop("expectedMemoryBytes", UNSET))

        reserved_bytes = d.pop("reservedBytes", UNSET)

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

        def _parse_error(data: object) -> ModelOutErrorType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                error_type_0 = ModelOutErrorType0.from_dict(data)

                return error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelOutErrorType0 | None | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_load_started_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                load_started_at_type_0 = datetime.datetime.fromisoformat(data)

                return load_started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        load_started_at = _parse_load_started_at(d.pop("loadStartedAt", UNSET))

        def _parse_ready_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ready_at_type_0 = datetime.datetime.fromisoformat(data)

                return ready_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        ready_at = _parse_ready_at(d.pop("readyAt", UNSET))

        def _parse_idle_unload_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                idle_unload_at_type_0 = datetime.datetime.fromisoformat(data)

                return idle_unload_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        idle_unload_at = _parse_idle_unload_at(d.pop("idleUnloadAt", UNSET))

        _active_leases = d.pop("activeLeases", UNSET)
        active_leases: list[ModelLeaseOut] | Unset = UNSET
        if _active_leases is not UNSET:
            active_leases = []
            for active_leases_item_data in _active_leases:
                active_leases_item = ModelLeaseOut.from_dict(active_leases_item_data)

                active_leases.append(active_leases_item)

        model_out = cls(
            id=id,
            display_name=display_name,
            family=family,
            download_state=download_state,
            memory_state=memory_state,
            backend=backend,
            stage=stage,
            disk_bytes=disk_bytes,
            download=download,
            expected_memory_bytes=expected_memory_bytes,
            reserved_bytes=reserved_bytes,
            context_limit=context_limit,
            capabilities=capabilities,
            validation=validation,
            license_=license_,
            error=error,
            load_started_at=load_started_at,
            ready_at=ready_at,
            idle_unload_at=idle_unload_at,
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
