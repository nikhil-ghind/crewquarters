from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backup_out import BackupOut


T = TypeVar("T", bound="BackupPage")


@_attrs_define
class BackupPage:
    """
    Attributes:
        items (list[BackupOut]):
        enabled (bool): False when CQ_BACKUP_DIR is not set on the device.
        location (None | str): Backup directory inside the platform.
        retention (int): Newest backups kept; older ones are deleted.
        includes (list[str]): What a backup contains.
        excludes (list[str]): What a backup never contains.
        next_cursor (None | str | Unset): Opaque cursor for the next page; null on the last page.
    """

    items: list[BackupOut]
    enabled: bool
    location: str | None
    retention: int
    includes: list[str]
    excludes: list[str]
    next_cursor: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        enabled = self.enabled

        location: str | None
        location = self.location

        retention = self.retention

        includes = self.includes

        excludes = self.excludes

        next_cursor: str | Unset | None
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "enabled": enabled,
                "location": location,
                "retention": retention,
                "includes": includes,
                "excludes": excludes,
            }
        )
        if next_cursor is not UNSET:
            field_dict["nextCursor"] = next_cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backup_out import BackupOut

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = BackupOut.from_dict(items_item_data)

            items.append(items_item)

        enabled = d.pop("enabled")

        def _parse_location(data: object) -> str | None:
            if data is None:
                return data
            return cast(None | str, data)

        location = _parse_location(d.pop("location"))

        retention = d.pop("retention")

        includes = cast(list[str], d.pop("includes"))

        excludes = cast(list[str], d.pop("excludes"))

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_cursor = _parse_next_cursor(d.pop("nextCursor", UNSET))

        backup_page = cls(
            items=items,
            enabled=enabled,
            location=location,
            retention=retention,
            includes=includes,
            excludes=excludes,
            next_cursor=next_cursor,
        )

        backup_page.additional_properties = d
        return backup_page

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
