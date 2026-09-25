"""A claim is retried after a lost response with the same X-Claim-Token, so the platform can
recognise the retry; separate claim() calls use different tokens."""

import httpx

from crewquarters._transport import BrokerClient
from crewquarters.idempotency import CLAIM_TOKEN_HEADER, IdempotencyClient


async def _no_sleep(_: float) -> None:
    return None


async def test_claim_retries_reuse_one_claim_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers[CLAIM_TOKEN_HEADER])
        if len(seen) == 1:
            raise httpx.ReadError("connection reset after the broker acted", request=request)
        return httpx.Response(200, json={"key": "k", "status": "claimed", "result": None})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = IdempotencyClient(BrokerClient("http://broker.test", "t", http=http, sleep=_no_sleep))
    record = await client.claim("k")
    assert record.status == "claimed"
    assert len(seen) == 2 and seen[0] == seen[1] and len(seen[0]) >= 16

    await client.claim("k")
    assert seen[2] != seen[0]
    await http.aclose()
