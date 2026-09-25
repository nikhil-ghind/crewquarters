from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model_out import ModelOut
from ...types import Response


def _get_kwargs(
    model_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/v1/models/{model_id}".format(
            model_id=quote(str(model_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ModelOut | None:
    if response.status_code == 200:
        response_200 = ModelOut.from_dict(response.json())

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
) -> Response[ErrorResponse | ModelOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ModelOut]:
    """Delete an installed model's files (it must not be loaded)

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelOut]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ModelOut | None:
    """Delete an installed model's files (it must not be loaded)

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelOut
    """

    return sync_detailed(
        model_id=model_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ModelOut]:
    """Delete an installed model's files (it must not be loaded)

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelOut]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ModelOut | None:
    """Delete an installed model's files (it must not be loaded)

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelOut
    """

    return (
        await asyncio_detailed(
            model_id=model_id,
            client=client,
        )
    ).parsed
