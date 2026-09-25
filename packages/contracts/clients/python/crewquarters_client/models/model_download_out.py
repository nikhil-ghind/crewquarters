from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelDownloadOut")


@_attrs_define
class ModelDownloadOut:
    """
    Attributes:
        bytes_done (int | Unset):  Default: 0.
        bytes_total (int | None | Unset):
        current_file (None | str | Unset):
        revision (None | str | Unset):
    """

    bytes_done: int | Unset = 0
    bytes_total: int | Unset | None = UNSET
    current_file: str | Unset | None = UNSET
    revision: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bytes_done = self.bytes_done

        bytes_total: int | Unset | None
        if isinstance(self.bytes_total, Unset):
            bytes_total = UNSET
        else:
            bytes_total = self.bytes_total

        current_file: str | Unset | None
        if isinstance(self.current_file, Unset):
            current_file = UNSET
        else:
            current_file = self.current_file

        revision: str | Unset | None
        if isinstance(self.revision, Unset):
            revision = UNSET
        else:
            revision = self.revision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if bytes_done is not UNSET:
            field_dict["bytesDone"] = bytes_done
        if bytes_total is not UNSET:
            field_dict["bytesTotal"] = bytes_total
        if current_file is not UNSET:
            field_dict["currentFile"] = current_file
        if revision is not UNSET:
            field_dict["revision"] = revision

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bytes_done = d.pop("bytesDone", UNSET)

        def _parse_bytes_total(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        bytes_total = _parse_bytes_total(d.pop("bytesTotal", UNSET))

        def _parse_current_file(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        current_file = _parse_current_file(d.pop("currentFile", UNSET))

        def _parse_revision(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        revision = _parse_revision(d.pop("revision", UNSET))

        model_download_out = cls(
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            current_file=current_file,
            revision=revision,
        )

        model_download_out.additional_properties = d
        return model_download_out

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
