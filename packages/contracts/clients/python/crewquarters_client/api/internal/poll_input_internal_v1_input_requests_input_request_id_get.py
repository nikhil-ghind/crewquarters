from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.input_request_out import InputRequestOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    input_request_id: UUID,
    *,
    wait: float | Unset = 0.0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["wait"] = wait

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/internal/v1/input-requests/{input_request_id}".format(
            input_request_id=quote(str(input_request_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | InputRequestOut | None:
    if response.status_code == 200:
        response_200 = InputRequestOut.from_dict(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = ErrorResponse.from_dict(response.json())

        return response_401

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | InputRequestOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    wait: float | Unset = 0.0,
) -> Response[ErrorResponse | InputRequestOut]:
    """Long-poll an input request until it is closed or the wait elapses

    Args:
        input_request_id (UUID):
        wait (float | Unset): Seconds to wait for the request to close. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InputRequestOut]
    """

    kwargs = _get_kwargs(
        input_request_id=input_request_id,
        wait=wait,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    wait: float | Unset = 0.0,
) -> ErrorResponse | InputRequestOut | None:
    """Long-poll an input request until it is closed or the wait elapses

    Args:
        input_request_id (UUID):
        wait (float | Unset): Seconds to wait for the request to close. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InputRequestOut
    """

    return sync_detailed(
        input_request_id=input_request_id,
        client=client,
        wait=wait,
    ).parsed


async def asyncio_detailed(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    wait: float | Unset = 0.0,
) -> Response[ErrorResponse | InputRequestOut]:
    """Long-poll an input request until it is closed or the wait elapses

    Args:
        input_request_id (UUID):
        wait (float | Unset): Seconds to wait for the request to close. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InputRequestOut]
    """

    kwargs = _get_kwargs(
        input_request_id=input_request_id,
        wait=wait,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    wait: float | Unset = 0.0,
) -> ErrorResponse | InputRequestOut | None:
    """Long-poll an input request until it is closed or the wait elapses

    Args:
        input_request_id (UUID):
        wait (float | Unset): Seconds to wait for the request to close. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InputRequestOut
    """

    return (
        await asyncio_detailed(
            input_request_id=input_request_id,
            client=client,
            wait=wait,
        )
    ).parsed
