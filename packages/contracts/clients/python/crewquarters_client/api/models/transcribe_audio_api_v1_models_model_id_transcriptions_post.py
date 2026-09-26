from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_transcribe_audio_api_v1_models_model_id_transcriptions_post import (
    BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
)
from ...models.error_response import ErrorResponse
from ...models.transcription_out import TranscriptionOut
from ...types import Response


def _get_kwargs(
    model_id: str,
    *,
    body: BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/models/{model_id}/transcriptions".format(
            model_id=quote(str(model_id), safe=""),
        ),
    }

    _kwargs["files"] = body.to_multipart()

    headers["Content-Type"] = "multipart/form-data; boundary=+++"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TranscriptionOut | None:
    if response.status_code == 200:
        response_200 = TranscriptionOut.from_dict(response.json())

        return response_200

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
) -> Response[ErrorResponse | TranscriptionOut]:
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
    body: BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
) -> Response[ErrorResponse | TranscriptionOut]:
    """Transcribe one audio file with a local speech-to-text model

     Loads the model on demand (admission control applies), so the first request can
    take as long as a cold start. The audio and the transcript are not stored; the audit
    record holds only the model, size, and outcome. Not replayable: each call transcribes.

    Args:
        model_id (str):
        body (BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TranscriptionOut]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
) -> ErrorResponse | TranscriptionOut | None:
    """Transcribe one audio file with a local speech-to-text model

     Loads the model on demand (admission control applies), so the first request can
    take as long as a cold start. The audio and the transcript are not stored; the audit
    record holds only the model, size, and outcome. Not replayable: each call transcribes.

    Args:
        model_id (str):
        body (BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TranscriptionOut
    """

    return sync_detailed(
        model_id=model_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
) -> Response[ErrorResponse | TranscriptionOut]:
    """Transcribe one audio file with a local speech-to-text model

     Loads the model on demand (admission control applies), so the first request can
    take as long as a cold start. The audio and the transcript are not stored; the audit
    record holds only the model, size, and outcome. Not replayable: each call transcribes.

    Args:
        model_id (str):
        body (BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TranscriptionOut]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost,
) -> ErrorResponse | TranscriptionOut | None:
    """Transcribe one audio file with a local speech-to-text model

     Loads the model on demand (admission control applies), so the first request can
    take as long as a cold start. The audio and the transcript are not stored; the audit
    record holds only the model, size, and outcome. Not replayable: each call transcribes.

    Args:
        model_id (str):
        body (BodyTranscribeAudioApiV1ModelsModelIdTranscriptionsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TranscriptionOut
    """

    return (
        await asyncio_detailed(
            model_id=model_id,
            client=client,
            body=body,
        )
    ).parsed
