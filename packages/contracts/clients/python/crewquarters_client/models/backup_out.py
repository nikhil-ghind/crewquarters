from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.backup_out_source import BackupOutSource
from ..models.backup_out_status import BackupOutStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backup_error_out import BackupErrorOut


T = TypeVar("T", bound="BackupOut")


@_attrs_define
class BackupOut:
    """
    Attributes:
        id (str): Backup name, e.g. crewquarters-backup-20260925T100000Z-1a2b3c
        status (BackupOutStatus):
        source (BackupOutSource): api: created from the UI/API; device: `crewquarters backup create`.
        created_at (datetime.datetime | None):
        finished_at (datetime.datetime | None):
        size_bytes (int | None):
        includes_master_key (bool): Never true for API backups. Backups with the master key are not downloadable through
            the API.
        platform_version (None | str):
        migration_head (None | str):
        document_count (int | None):
        downloadable (bool):
        error (BackupErrorOut | None):
        sha256 (None | str | Unset): SHA-256 of the archive file.
    """

    id: str
    status: BackupOutStatus
    source: BackupOutSource
    created_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    size_bytes: int | None
    includes_master_key: bool
    platform_version: str | None
    migration_head: str | None
    document_count: int | None
    downloadable: bool
    error: BackupErrorOut | None
    sha256: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.backup_error_out import BackupErrorOut

        id = self.id

        status = self.status.value

        source = self.source.value

        created_at: str | None
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        finished_at: str | None
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        size_bytes: int | None
        size_bytes = self.size_bytes

        includes_master_key = self.includes_master_key

        platform_version: str | None
        platform_version = self.platform_version

        migration_head: str | None
        migration_head = self.migration_head

        document_count: int | None
        document_count = self.document_count

        downloadable = self.downloadable

        error: dict[str, Any] | None
        if isinstance(self.error, BackupErrorOut):
            error = self.error.to_dict()
        else:
            error = self.error

        sha256: str | Unset | None
        if isinstance(self.sha256, Unset):
            sha256 = UNSET
        else:
            sha256 = self.sha256

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "status": status,
                "source": source,
                "createdAt": created_at,
                "finishedAt": finished_at,
                "sizeBytes": size_bytes,
                "includesMasterKey": includes_master_key,
                "platformVersion": platform_version,
                "migrationHead": migration_head,
                "documentCount": document_count,
                "downloadable": downloadable,
                "error": error,
            }
        )
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backup_error_out import BackupErrorOut

        d = dict(src_dict)
        id = d.pop("id")

        status = BackupOutStatus(d.pop("status"))

        source = BackupOutSource(d.pop("source"))

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = datetime.datetime.fromisoformat(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("createdAt"))

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

        def _parse_size_bytes(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        size_bytes = _parse_size_bytes(d.pop("sizeBytes"))

        includes_master_key = d.pop("includesMasterKey")

        def _parse_platform_version(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        platform_version = _parse_platform_version(d.pop("platformVersion"))

        def _parse_migration_head(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        migration_head = _parse_migration_head(d.pop("migrationHead"))

        def _parse_document_count(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        document_count = _parse_document_count(d.pop("documentCount"))

        downloadable = d.pop("downloadable")

        def _parse_error(data: object) -> BackupErrorOut | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                error_type_0 = BackupErrorOut.from_dict(data)

                return error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackupErrorOut | None, data)

        error = _parse_error(d.pop("error"))

        def _parse_sha256(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sha256 = _parse_sha256(d.pop("sha256", UNSET))

        backup_out = cls(
            id=id,
            status=status,
            source=source,
            created_at=created_at,
            finished_at=finished_at,
            size_bytes=size_bytes,
            includes_master_key=includes_master_key,
            platform_version=platform_version,
            migration_head=migration_head,
            document_count=document_count,
            downloadable=downloadable,
            error=error,
            sha256=sha256,
        )

        backup_out.additional_properties = d
        return backup_out

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
