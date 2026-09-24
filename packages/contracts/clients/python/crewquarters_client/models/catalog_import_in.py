from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.catalog_import_in_manifest import CatalogImportInManifest


T = TypeVar("T", bound="CatalogImportIn")


@_attrs_define
class CatalogImportIn:
    """
    Attributes:
        manifest (CatalogImportInManifest): Agent manifest (crewquarters/v1alpha1).
    """

    manifest: CatalogImportInManifest
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        manifest = self.manifest.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "manifest": manifest,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.catalog_import_in_manifest import CatalogImportInManifest

        d = dict(src_dict)
        manifest = CatalogImportInManifest.from_dict(d.pop("manifest"))

        catalog_import_in = cls(
            manifest=manifest,
        )

        catalog_import_in.additional_properties = d
        return catalog_import_in

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
