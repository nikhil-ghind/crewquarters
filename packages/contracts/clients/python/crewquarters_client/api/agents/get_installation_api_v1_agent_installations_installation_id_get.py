from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.installation_out import InstallationOut
from ...types import Response


def _get_kwargs(
    installation_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/agent-installations/{installation_id}".format(
            installation_id=quote(str(installation_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | InstallationOut | None:
    if response.status_code == 200:
        response_200 = InstallationOut.from_dict(response.json())

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
) -> Response[ErrorResponse | InstallationOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    installation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | InstallationOut]:
    """Get an installed agent with readiness

    Args:
        installation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InstallationOut]
    """

    kwargs = _get_kwargs(
        installation_id=installation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    installation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | InstallationOut | None:
    """Get an installed agent with readiness

    Args:
        installation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InstallationOut
    """

    return sync_detailed(
        installation_id=installation_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    installation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | InstallationOut]:
    """Get an installed agent with readiness

    Args:
        installation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InstallationOut]
    """

    kwargs = _get_kwargs(
        installation_id=installation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    installation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | InstallationOut | None:
    """Get an installed agent with readiness

    Args:
        installation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InstallationOut
    """

    return (
        await asyncio_detailed(
            installation_id=installation_id,
            client=client,
        )
    ).parsed
