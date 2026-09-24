from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.run_result_in_status import RunResultInStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_result_in_error_type_0 import RunResultInErrorType0
    from ..models.run_result_in_result_type_0 import RunResultInResultType0


T = TypeVar("T", bound="RunResultIn")


@_attrs_define
class RunResultIn:
    """
    Attributes:
        attempt (int):
        status (RunResultInStatus):
        result (None | RunResultInResultType0 | Unset):
        error (None | RunResultInErrorType0 | Unset):
    """

    attempt: int
    status: RunResultInStatus
    result: RunResultInResultType0 | Unset | None = UNSET
    error: RunResultInErrorType0 | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.run_result_in_error_type_0 import RunResultInErrorType0
        from ..models.run_result_in_result_type_0 import RunResultInResultType0

        attempt = self.attempt

        status = self.status.value

        result: dict[str, Any] | Unset | None
        if isinstance(self.result, Unset):
            result = UNSET
        elif isinstance(self.result, RunResultInResultType0):
            result = self.result.to_dict()
        else:
            result = self.result

        error: dict[str, Any] | Unset | None
        if isinstance(self.error, Unset):
            error = UNSET
        elif isinstance(self.error, RunResultInErrorType0):
            error = self.error.to_dict()
        else:
            error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempt": attempt,
                "status": status,
            }
        )
        if result is not UNSET:
            field_dict["result"] = result
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_result_in_error_type_0 import RunResultInErrorType0
        from ..models.run_result_in_result_type_0 import RunResultInResultType0

        d = dict(src_dict)
        attempt = d.pop("attempt")

        status = RunResultInStatus(d.pop("status"))

        def _parse_result(data: object) -> RunResultInResultType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                result_type_0 = RunResultInResultType0.from_dict(data)

                return result_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunResultInResultType0 | Unset, data)

        result = _parse_result(d.pop("result", UNSET))

        def _parse_error(data: object) -> RunResultInErrorType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                error_type_0 = RunResultInErrorType0.from_dict(data)

                return error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunResultInErrorType0 | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        run_result_in = cls(
            attempt=attempt,
            status=status,
            result=result,
            error=error,
        )

        run_result_in.additional_properties = d
        return run_result_in

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
