from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.knowledge_query_in import KnowledgeQueryIn
from ...models.knowledge_query_out import KnowledgeQueryOut
from ...types import Response


def _get_kwargs(
    kb_id: UUID,
    *,
    body: KnowledgeQueryIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/knowledge-bases/{kb_id}/query".format(
            kb_id=quote(str(kb_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | KnowledgeQueryOut | None:
    if response.status_code == 200:
        response_200 = KnowledgeQueryOut.from_dict(response.json())

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
) -> Response[ErrorResponse | KnowledgeQueryOut]:
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
    body: KnowledgeQueryIn,
) -> Response[ErrorResponse | KnowledgeQueryOut]:
    """Test retrieval: cited passages from this knowledge base only

     Read-only, so no idempotency record. Passages are untrusted document text.

    Args:
        kb_id (UUID):
        body (KnowledgeQueryIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | KnowledgeQueryOut]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: KnowledgeQueryIn,
) -> ErrorResponse | KnowledgeQueryOut | None:
    """Test retrieval: cited passages from this knowledge base only

     Read-only, so no idempotency record. Passages are untrusted document text.

    Args:
        kb_id (UUID):
        body (KnowledgeQueryIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | KnowledgeQueryOut
    """

    return sync_detailed(
        kb_id=kb_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: KnowledgeQueryIn,
) -> Response[ErrorResponse | KnowledgeQueryOut]:
    """Test retrieval: cited passages from this knowledge base only

     Read-only, so no idempotency record. Passages are untrusted document text.

    Args:
        kb_id (UUID):
        body (KnowledgeQueryIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | KnowledgeQueryOut]
    """

    kwargs = _get_kwargs(
        kb_id=kb_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    kb_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: KnowledgeQueryIn,
) -> ErrorResponse | KnowledgeQueryOut | None:
    """Test retrieval: cited passages from this knowledge base only

     Read-only, so no idempotency record. Passages are untrusted document text.

    Args:
        kb_id (UUID):
        body (KnowledgeQueryIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | KnowledgeQueryOut
    """

    return (
        await asyncio_detailed(
            kb_id=kb_id,
            client=client,
            body=body,
        )
    ).parsed
