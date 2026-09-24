from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_runs_api_v1_runs_get_state_type_0_item import ListRunsApiV1RunsGetStateType0Item
from ...models.page_run_out import PageRunOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    installation_id: None | Unset | UUID = UNSET,
    state: list[ListRunsApiV1RunsGetStateType0Item] | None | Unset = UNSET,
    trigger: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_installation_id: str | Unset | None
    if isinstance(installation_id, Unset):
        json_installation_id = UNSET
    elif isinstance(installation_id, UUID):
        json_installation_id = str(installation_id)
    else:
        json_installation_id = installation_id
    params["installationId"] = json_installation_id

    json_state: list[str] | Unset | None
    if isinstance(state, Unset):
        json_state = UNSET
    elif isinstance(state, list):
        json_state = []
        for state_type_0_item_data in state:
            state_type_0_item = state_type_0_item_data.value
            json_state.append(state_type_0_item)

    else:
        json_state = state
    params["state"] = json_state

    json_trigger: str | Unset | None
    if isinstance(trigger, Unset):
        json_trigger = UNSET
    else:
        json_trigger = trigger
    params["trigger"] = json_trigger

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
        "url": "/api/v1/runs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PageRunOut | None:
    if response.status_code == 200:
        response_200 = PageRunOut.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | PageRunOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    installation_id: None | Unset | UUID = UNSET,
    state: list[ListRunsApiV1RunsGetStateType0Item] | None | Unset = UNSET,
    trigger: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageRunOut]:
    """List runs, newest first

    Args:
        installation_id (None | Unset | UUID):
        state (list[ListRunsApiV1RunsGetStateType0Item] | None | Unset):
        trigger (None | str | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageRunOut]
    """

    kwargs = _get_kwargs(
        installation_id=installation_id,
        state=state,
        trigger=trigger,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    installation_id: None | Unset | UUID = UNSET,
    state: list[ListRunsApiV1RunsGetStateType0Item] | None | Unset = UNSET,
    trigger: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | PageRunOut | None:
    """List runs, newest first

    Args:
        installation_id (None | Unset | UUID):
        state (list[ListRunsApiV1RunsGetStateType0Item] | None | Unset):
        trigger (None | str | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageRunOut
    """

    return sync_detailed(
        client=client,
        installation_id=installation_id,
        state=state,
        trigger=trigger,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    installation_id: None | Unset | UUID = UNSET,
    state: list[ListRunsApiV1RunsGetStateType0Item] | None | Unset = UNSET,
    trigger: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageRunOut]:
    """List runs, newest first

    Args:
        installation_id (None | Unset | UUID):
        state (list[ListRunsApiV1RunsGetStateType0Item] | None | Unset):
        trigger (None | str | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageRunOut]
    """

    kwargs = _get_kwargs(
        installation_id=installation_id,
        state=state,
        trigger=trigger,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    installation_id: None | Unset | UUID = UNSET,
    state: list[ListRunsApiV1RunsGetStateType0Item] | None | Unset = UNSET,
    trigger: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | PageRunOut | None:
    """List runs, newest first

    Args:
        installation_id (None | Unset | UUID):
        state (list[ListRunsApiV1RunsGetStateType0Item] | None | Unset):
        trigger (None | str | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageRunOut
    """

    return (
        await asyncio_detailed(
            client=client,
            installation_id=installation_id,
            state=state,
            trigger=trigger,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
