from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.voice_call_in import VoiceCallIn
from ...models.voice_call_out import VoiceCallOut
from ...types import Response


def _get_kwargs(
    *,
    body: VoiceCallIn,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connections/twilio/voice-calls",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | VoiceCallOut | None:
    if response.status_code == 200:
        response_200 = VoiceCallOut.from_dict(response.json())

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

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

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
) -> Response[ErrorResponse | VoiceCallOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: VoiceCallIn,
) -> Response[ErrorResponse | VoiceCallOut]:
    """Start a realtime AI voice call to an allowed number (the owner must confirm)

     Twilio speaks the automated-call disclosure, then the caller talks with the local
    models (speech-to-text, chat, text-to-speech). One call at a time; a retry with the same
    ``Idempotency-Key`` replays the first result and never dials twice.

    Args:
        body (VoiceCallIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | VoiceCallOut]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: VoiceCallIn,
) -> ErrorResponse | VoiceCallOut | None:
    """Start a realtime AI voice call to an allowed number (the owner must confirm)

     Twilio speaks the automated-call disclosure, then the caller talks with the local
    models (speech-to-text, chat, text-to-speech). One call at a time; a retry with the same
    ``Idempotency-Key`` replays the first result and never dials twice.

    Args:
        body (VoiceCallIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | VoiceCallOut
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: VoiceCallIn,
) -> Response[ErrorResponse | VoiceCallOut]:
    """Start a realtime AI voice call to an allowed number (the owner must confirm)

     Twilio speaks the automated-call disclosure, then the caller talks with the local
    models (speech-to-text, chat, text-to-speech). One call at a time; a retry with the same
    ``Idempotency-Key`` replays the first result and never dials twice.

    Args:
        body (VoiceCallIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | VoiceCallOut]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: VoiceCallIn,
) -> ErrorResponse | VoiceCallOut | None:
    """Start a realtime AI voice call to an allowed number (the owner must confirm)

     Twilio speaks the automated-call disclosure, then the caller talks with the local
    models (speech-to-text, chat, text-to-speech). One call at a time; a retry with the same
    ``Idempotency-Key`` replays the first result and never dials twice.

    Args:
        body (VoiceCallIn):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | VoiceCallOut
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
