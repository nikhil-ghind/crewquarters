import datetime
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.page_audit_event_out import PageAuditEventOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    action: None | str | Unset = UNSET,
    actor_id: None | str | Unset = UNSET,
    target_id: None | str | Unset = UNSET,
    outcome: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_action: str | Unset | None
    if isinstance(action, Unset):
        json_action = UNSET
    else:
        json_action = action
    params["action"] = json_action

    json_actor_id: str | Unset | None
    if isinstance(actor_id, Unset):
        json_actor_id = UNSET
    else:
        json_actor_id = actor_id
    params["actorId"] = json_actor_id

    json_target_id: str | Unset | None
    if isinstance(target_id, Unset):
        json_target_id = UNSET
    else:
        json_target_id = target_id
    params["targetId"] = json_target_id

    json_outcome: str | Unset | None
    if isinstance(outcome, Unset):
        json_outcome = UNSET
    else:
        json_outcome = outcome
    params["outcome"] = json_outcome

    json_since: str | Unset | None
    if isinstance(since, Unset):
        json_since = UNSET
    elif isinstance(since, datetime.datetime):
        json_since = since.isoformat()
    else:
        json_since = since
    params["since"] = json_since

    json_until: str | Unset | None
    if isinstance(until, Unset):
        json_until = UNSET
    elif isinstance(until, datetime.datetime):
        json_until = until.isoformat()
    else:
        json_until = until
    params["until"] = json_until

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
        "url": "/api/v1/audit-events",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PageAuditEventOut | None:
    if response.status_code == 200:
        response_200 = PageAuditEventOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | PageAuditEventOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    action: None | str | Unset = UNSET,
    actor_id: None | str | Unset = UNSET,
    target_id: None | str | Unset = UNSET,
    outcome: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageAuditEventOut]:
    """Audit history (owner only)

    Args:
        action (None | str | Unset): Exact action or prefix ending in '*', e.g. auth.*
        actor_id (None | str | Unset):
        target_id (None | str | Unset):
        outcome (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageAuditEventOut]
    """

    kwargs = _get_kwargs(
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        outcome=outcome,
        since=since,
        until=until,
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
    action: None | str | Unset = UNSET,
    actor_id: None | str | Unset = UNSET,
    target_id: None | str | Unset = UNSET,
    outcome: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | PageAuditEventOut | None:
    """Audit history (owner only)

    Args:
        action (None | str | Unset): Exact action or prefix ending in '*', e.g. auth.*
        actor_id (None | str | Unset):
        target_id (None | str | Unset):
        outcome (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageAuditEventOut
    """

    return sync_detailed(
        client=client,
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        outcome=outcome,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    action: None | str | Unset = UNSET,
    actor_id: None | str | Unset = UNSET,
    target_id: None | str | Unset = UNSET,
    outcome: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageAuditEventOut]:
    """Audit history (owner only)

    Args:
        action (None | str | Unset): Exact action or prefix ending in '*', e.g. auth.*
        actor_id (None | str | Unset):
        target_id (None | str | Unset):
        outcome (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageAuditEventOut]
    """

    kwargs = _get_kwargs(
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        outcome=outcome,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    action: None | str | Unset = UNSET,
    actor_id: None | str | Unset = UNSET,
    target_id: None | str | Unset = UNSET,
    outcome: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    cursor: None | str | Unset = UNSET,
) -> HTTPValidationError | PageAuditEventOut | None:
    """Audit history (owner only)

    Args:
        action (None | str | Unset): Exact action or prefix ending in '*', e.g. auth.*
        actor_id (None | str | Unset):
        target_id (None | str | Unset):
        outcome (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageAuditEventOut
    """

    return (
        await asyncio_detailed(
            client=client,
            action=action,
            actor_id=actor_id,
            target_id=target_id,
            outcome=outcome,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
