from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.page_document_out import PageDocumentOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    kb_id: UUID,
    *,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/knowledge-bases/{kb_id}/documents".format(
            kb_id=quote(str(kb_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PageDocumentOut | None:
    if response.status_code == 200:
        response_200 = PageDocumentOut.from_dict(response.json())

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

    if response.status_code == 502:
        response_502 = ErrorResponse.from_dict(response.json())

        return response_502

    if response.status_code == 503:
        response_503 = ErrorResponse.from_dict(response.json())

        return response_503

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | PageDocumentOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | PageDocumentOut]:
    """Documents with their per-file ingestion state and errors

    Args:
        kb_id (UUID):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PageDocumentOut]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | PageDocumentOut | None:
    """Documents with their per-file ingestion state and errors

    Args:
        kb_id (UUID):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PageDocumentOut
    """

    return sync_detailed(
        kb_id=kb_id,
        client=client,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | PageDocumentOut]:
    """Documents with their per-file ingestion state and errors

    Args:
        kb_id (UUID):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PageDocumentOut]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | PageDocumentOut | None:
    """Documents with their per-file ingestion state and errors

    Args:
        kb_id (UUID):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PageDocumentOut
    """

    return (
        await asyncio_detailed(
            kb_id=kb_id,
            client=client,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
