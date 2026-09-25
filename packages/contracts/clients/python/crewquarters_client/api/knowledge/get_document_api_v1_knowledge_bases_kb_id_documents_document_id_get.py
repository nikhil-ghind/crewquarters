from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.document_out import DocumentOut
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    kb_id: UUID,
    document_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/knowledge-bases/{kb_id}/documents/{document_id}".format(
            kb_id=quote(str(kb_id), safe=""),
            document_id=quote(str(document_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DocumentOut | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DocumentOut.from_dict(response.json())

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
) -> Response[DocumentOut | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    kb_id: UUID,
    document_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DocumentOut | ErrorResponse]:
    """One document

    Args:
        kb_id (UUID):
        document_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentOut | ErrorResponse]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        document_id=document_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    kb_id: UUID,
    document_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> DocumentOut | ErrorResponse | None:
    """One document

    Args:
        kb_id (UUID):
        document_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentOut | ErrorResponse
    """

    return sync_detailed(
        kb_id=kb_id,
        document_id=document_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    kb_id: UUID,
    document_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DocumentOut | ErrorResponse]:
    """One document

    Args:
        kb_id (UUID):
        document_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentOut | ErrorResponse]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        document_id=document_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    kb_id: UUID,
    document_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> DocumentOut | ErrorResponse | None:
    """One document

    Args:
        kb_id (UUID):
        document_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentOut | ErrorResponse
    """

    return (
        await asyncio_detailed(
            kb_id=kb_id,
            document_id=document_id,
            client=client,
        )
    ).parsed
