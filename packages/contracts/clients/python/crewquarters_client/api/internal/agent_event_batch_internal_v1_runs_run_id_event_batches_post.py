from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.event_batch_in import EventBatchIn
from ...models.event_batch_out import EventBatchOut
from ...types import Response


def _get_kwargs(
    run_id: UUID,
    *,
    body: EventBatchIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/internal/v1/runs/{run_id}/event-batches".format(
            run_id=quote(str(run_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | EventBatchOut | None:
    if response.status_code == 200:
        response_200 = EventBatchOut.from_dict(response.json())

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
) -> Response[ErrorResponse | EventBatchOut]:
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
    body: EventBatchIn,
) -> Response[ErrorResponse | EventBatchOut]:
    """Append a batch of agent events atomically, deduplicated by clientEventId

     Every event is validated first (type, size, and the run-event schema); if any is
    invalid nothing is stored and ``422 INVALID_EVENT`` lists each rejected event as
    ``details.rejected[] = {index, clientEventId, code, errors}``. Otherwise all new events
    are inserted in one transaction, and a ``clientEventId`` already stored for the run is
    skipped, so a retried batch never duplicates events.

    Args:
        run_id (UUID):
        body (EventBatchIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | EventBatchOut]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: EventBatchIn,
) -> ErrorResponse | EventBatchOut | None:
    """Append a batch of agent events atomically, deduplicated by clientEventId

     Every event is validated first (type, size, and the run-event schema); if any is
    invalid nothing is stored and ``422 INVALID_EVENT`` lists each rejected event as
    ``details.rejected[] = {index, clientEventId, code, errors}``. Otherwise all new events
    are inserted in one transaction, and a ``clientEventId`` already stored for the run is
    skipped, so a retried batch never duplicates events.

    Args:
        run_id (UUID):
        body (EventBatchIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | EventBatchOut
    """

    return sync_detailed(
        run_id=run_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: EventBatchIn,
) -> Response[ErrorResponse | EventBatchOut]:
    """Append a batch of agent events atomically, deduplicated by clientEventId

     Every event is validated first (type, size, and the run-event schema); if any is
    invalid nothing is stored and ``422 INVALID_EVENT`` lists each rejected event as
    ``details.rejected[] = {index, clientEventId, code, errors}``. Otherwise all new events
    are inserted in one transaction, and a ``clientEventId`` already stored for the run is
    skipped, so a retried batch never duplicates events.

    Args:
        run_id (UUID):
        body (EventBatchIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | EventBatchOut]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: EventBatchIn,
) -> ErrorResponse | EventBatchOut | None:
    """Append a batch of agent events atomically, deduplicated by clientEventId

     Every event is validated first (type, size, and the run-event schema); if any is
    invalid nothing is stored and ``422 INVALID_EVENT`` lists each rejected event as
    ``details.rejected[] = {index, clientEventId, code, errors}``. Otherwise all new events
    are inserted in one transaction, and a ``clientEventId`` already stored for the run is
    skipped, so a retried batch never duplicates events.

    Args:
        run_id (UUID):
        body (EventBatchIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | EventBatchOut
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            body=body,
        )
    ).parsed
