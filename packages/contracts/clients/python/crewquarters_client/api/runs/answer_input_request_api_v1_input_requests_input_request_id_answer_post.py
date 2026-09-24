from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.input_answer_in import InputAnswerIn
from ...models.input_request_out import InputRequestOut
from ...types import Response


def _get_kwargs(
    input_request_id: UUID,
    *,
    body: InputAnswerIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/input-requests/{input_request_id}/answer".format(
            input_request_id=quote(str(input_request_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | InputRequestOut | None:
    if response.status_code == 200:
        response_200 = InputRequestOut.from_dict(response.json())

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
    body: InputAnswerIn,
) -> Response[ErrorResponse | InputRequestOut]:
    """Answer a Crew Request (rejects stale versions and double answers)

    Args:
        input_request_id (UUID):
        body (InputAnswerIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InputRequestOut]
    """

    kwargs = _get_kwargs(
        input_request_id=input_request_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InputAnswerIn,
) -> ErrorResponse | InputRequestOut | None:
    """Answer a Crew Request (rejects stale versions and double answers)

    Args:
        input_request_id (UUID):
        body (InputAnswerIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InputRequestOut
    """

    return sync_detailed(
        input_request_id=input_request_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InputAnswerIn,
) -> Response[ErrorResponse | InputRequestOut]:
    """Answer a Crew Request (rejects stale versions and double answers)

    Args:
        input_request_id (UUID):
        body (InputAnswerIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InputRequestOut]
    """

    kwargs = _get_kwargs(
        input_request_id=input_request_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    input_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InputAnswerIn,
) -> ErrorResponse | InputRequestOut | None:
    """Answer a Crew Request (rejects stale versions and double answers)

    Args:
        input_request_id (UUID):
        body (InputAnswerIn):

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
            body=body,
        )
    ).parsed
