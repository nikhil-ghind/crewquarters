"""Gmail client: list message ids (with pagination) and fetch parsed messages."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from crewquarters._transport import BrokerClient
from crewquarters.google.mime import GmailMessage, parse_message

PAGE_SIZE = 100


@dataclass(frozen=True)
class MessageIdPage:
    ids: list[tuple[str, str]]
    next_page_token: str | None
    result_size_estimate: int


class IdIteration:
    """Async iterator over ``(message_id, thread_id)`` that stops at ``limit``.

    ``truncated`` becomes true when iteration stopped at the limit while more messages existed.
    """

    def __init__(
        self, client: GmailClient, query: str, limit: int, label_ids: list[str] | None
    ) -> None:
        self._client = client
        self._query = query
        self._limit = limit
        self._label_ids = label_ids
        self.truncated = False

    def __aiter__(self) -> AsyncIterator[tuple[str, str]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[tuple[str, str]]:
        count = 0
        token: str | None = None
        while True:
            page = await self._client.list_message_ids(
                self._query, max_results=PAGE_SIZE, page_token=token, label_ids=self._label_ids
            )
            for ident in page.ids:
                if count >= self._limit:
                    self.truncated = True
                    return
                yield ident
                count += 1
            token = page.next_page_token
            if not token:
                return
            if count >= self._limit:
                self.truncated = True
                return


class GmailClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def list_message_ids(
        self,
        query: str,
        *,
        max_results: int = PAGE_SIZE,
        page_token: str | None = None,
        label_ids: list[str] | None = None,
    ) -> MessageIdPage:
        params: dict[str, Any] = {"q": query, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = label_ids
        data = await self._transport.request(
            "GET", "/google/gmail/messages", operation="gmail.list", idempotent=True, params=params
        )
        ids = [(str(m["id"]), str(m.get("threadId", m["id"]))) for m in data.get("messages", [])]
        return MessageIdPage(
            ids, data.get("nextPageToken"), int(data.get("resultSizeEstimate", len(ids)))
        )

    def iter_message_ids(
        self, query: str, *, limit: int, label_ids: list[str] | None = None
    ) -> IdIteration:
        return IdIteration(self, query, limit, label_ids)

    async def notify_owner(
        self, subject: str, text: str, image: dict[str, Any] | None = None
    ) -> str:
        """Email the owner's own Gmail address (the broker sets the recipient). Returns the
        Gmail message id. Not retried: a lost response could otherwise send twice."""
        body: dict[str, Any] = {"subject": subject, "text": text}
        if image is not None:
            body["image"] = {"mediaType": image["mediaType"], "data": image["data"]}
        data = await self._transport.request(
            "POST",
            "/google/gmail/notify-owner",
            operation="gmail.notify",
            idempotent=False,
            json=body,
        )
        return str(data["id"])

    async def get_message(self, message_id: str, *, max_chars: int = 4000) -> GmailMessage:
        data = await self._transport.request(
            "GET",
            f"/google/gmail/messages/{quote(message_id, safe='')}",
            operation="gmail.get",
            idempotent=True,
        )
        return parse_message(data if isinstance(data, dict) else {}, max_chars=max_chars)
