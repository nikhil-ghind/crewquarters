from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.run_event_out import RunEventOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    run_id: UUID,
    *,
    after: int | Unset = 0,
    limit: int | Unset = 200,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["after"] = after

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs/{run_id}/events/history".format(
            run_id=quote(str(run_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | list[RunEventOut] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RunEventOut.from_dict(response_200_item_data)

            response_200.append(response_200_item)

        return response_200

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
) -> Response[ErrorResponse | list[RunEventOut]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    limit: int | Unset = 200,
) -> Response[ErrorResponse | list[RunEventOut]]:
    """Run events as JSON (polling fallback for SSE)

    Args:
        run_id (UUID):
        after (int | Unset): Return events with sequence greater than this. Default: 0.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[RunEventOut]]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        after=after,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    limit: int | Unset = 200,
) -> ErrorResponse | list[RunEventOut] | None:
    """Run events as JSON (polling fallback for SSE)

    Args:
        run_id (UUID):
        after (int | Unset): Return events with sequence greater than this. Default: 0.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[RunEventOut]
    """

    return sync_detailed(
        run_id=run_id,
        client=client,
        after=after,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    limit: int | Unset = 200,
) -> Response[ErrorResponse | list[RunEventOut]]:
    """Run events as JSON (polling fallback for SSE)

    Args:
        run_id (UUID):
        after (int | Unset): Return events with sequence greater than this. Default: 0.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[RunEventOut]]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        after=after,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    limit: int | Unset = 200,
) -> ErrorResponse | list[RunEventOut] | None:
    """Run events as JSON (polling fallback for SSE)

    Args:
        run_id (UUID):
        after (int | Unset): Return events with sequence greater than this. Default: 0.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[RunEventOut]
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            after=after,
            limit=limit,
        )
    ).parsed
