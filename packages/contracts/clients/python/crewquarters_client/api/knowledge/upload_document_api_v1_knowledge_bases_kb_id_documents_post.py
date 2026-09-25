from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_upload_document_api_v1_knowledge_bases_kb_id_documents_post import (
    BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
)
from ...models.document_out import DocumentOut
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    kb_id: UUID,
    *,
    body: BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/knowledge-bases/{kb_id}/documents".format(
            kb_id=quote(str(kb_id), safe=""),
        ),
    }

    _kwargs["files"] = body.to_multipart()

    headers["Content-Type"] = "multipart/form-data; boundary=+++"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DocumentOut | ErrorResponse | None:
    if response.status_code == 202:
        response_202 = DocumentOut.from_dict(response.json())

        return response_202

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 413:
        response_413 = ErrorResponse.from_dict(response.json())

        return response_413

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
    *,
    client: AuthenticatedClient | Client,
    body: BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
) -> Response[DocumentOut | ErrorResponse]:
    """Upload one document (multipart `file`); indexing is asynchronous

     The body limit is ``CQ_MAX_UPLOAD_BYTES`` (25 MiB) rather than the global 2 MiB; the
    knowledge service validates type, name, size, and duplicates. Not replayable: a retry
    of the same bytes returns ``409 DUPLICATE_DOCUMENT`` with the existing document id.

    Args:
        kb_id (UUID):
        body (BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentOut | ErrorResponse]
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
    body: BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
) -> DocumentOut | ErrorResponse | None:
    """Upload one document (multipart `file`); indexing is asynchronous

     The body limit is ``CQ_MAX_UPLOAD_BYTES`` (25 MiB) rather than the global 2 MiB; the
    knowledge service validates type, name, size, and duplicates. Not replayable: a retry
    of the same bytes returns ``409 DUPLICATE_DOCUMENT`` with the existing document id.

    Args:
        kb_id (UUID):
        body (BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentOut | ErrorResponse
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
    body: BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
) -> Response[DocumentOut | ErrorResponse]:
    """Upload one document (multipart `file`); indexing is asynchronous

     The body limit is ``CQ_MAX_UPLOAD_BYTES`` (25 MiB) rather than the global 2 MiB; the
    knowledge service validates type, name, size, and duplicates. Not replayable: a retry
    of the same bytes returns ``409 DUPLICATE_DOCUMENT`` with the existing document id.

    Args:
        kb_id (UUID):
        body (BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentOut | ErrorResponse]
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
    body: BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost,
) -> DocumentOut | ErrorResponse | None:
    """Upload one document (multipart `file`); indexing is asynchronous

     The body limit is ``CQ_MAX_UPLOAD_BYTES`` (25 MiB) rather than the global 2 MiB; the
    knowledge service validates type, name, size, and duplicates. Not replayable: a retry
    of the same bytes returns ``409 DUPLICATE_DOCUMENT`` with the existing document id.

    Args:
        kb_id (UUID):
        body (BodyUploadDocumentApiV1KnowledgeBasesKbIdDocumentsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentOut | ErrorResponse
    """

    return (
        await asyncio_detailed(
            kb_id=kb_id,
            client=client,
            body=body,
        )
    ).parsed
