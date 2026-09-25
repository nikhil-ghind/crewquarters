"""Person 5's real SDK clients against this broker: the contract, exercised end to end."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from crewquarters._transport import BrokerClient
from crewquarters.google import GoogleClients
from crewquarters.idempotency import IdempotencyClient
from crewquarters.knowledge import KnowledgeClient
from crewquarters.llm import LLMClient
from crewquarters.telephony import TelephonyClient

SID = "AC" + "0" * 31 + "1"
SCRIPT = "Hello {name}. This is an automated demo call from the Crewquarters team."
DISCLOSURE = "This is an automated demonstration call. Your spoken reply will be transcribed."


def _sdk(h: Any, headers: dict[str, str]) -> BrokerClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app))
    return BrokerClient(
        "http://broker", headers["authorization"].removeprefix("Bearer "), http=http
    )


async def test_sdk_google_knowledge_and_llm(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(
        [
            "google.gmail.readonly",
            "google.spreadsheets",
            "knowledge.search:config",
            "llm.profile:local.general.small",
        ],
        config={
            "spreadsheetId": harness.SPREADSHEET,
            "knowledgeBaseId": harness.KB_ID,
            "inputRange": "Results!A:B",
            "resultRange": "Results!A:B",
        },
    )
    transport = _sdk(harness, headers)
    google = GoogleClients(transport)

    page = await google.gmail.list_message_ids("", max_results=5)
    assert page.result_size_estimate == 8 and len(page.ids) == 5
    message = await google.gmail.get_message("m-multipart")
    assert message.text_body == "Invoice 42 is overdue." and message.subject == "Invoice overdue"

    await google.sheets.update_values(harness.SPREADSHEET, "Results!A1", [["name", "status"]])
    await google.sheets.append_values(harness.SPREADSHEET, "Results!A:B", [["Asha", "called"]])
    assert await google.sheets.get_values(harness.SPREADSHEET, "Results!A1:B") == [
        ["name", "status"],
        ["Asha", "called"],
    ]

    result = await KnowledgeClient(transport).search(harness.KB_ID, "refunds", top_k=3)
    assert [(p.citation_id, p.document_name) for p in result.passages] == [("c1", "policy.md")]

    chat = await LLMClient(transport, ["local.general.small"]).chat(
        "local.general.small", [{"role": "user", "content": "hi"}]
    )
    assert chat.text == "Hi."


async def test_sdk_places_a_call_once(
    harness: Any, user_id: uuid.UUID, real_run_id: uuid.UUID
) -> None:
    await harness.client.put(
        "/internal/v1/connections/twilio",
        headers=harness.service_headers,
        json={
            "userId": str(user_id),
            "accountSid": SID,
            "authToken": "auth-token-value-0001",
            "fromNumber": "+15555550199",
        },
    )
    headers = harness.agent(
        ["twilio.call.fixed_script", "idempotency"],
        run_id=real_run_id,
        config={"script": SCRIPT, "disclosure": DISCLOSURE},
    )
    transport = _sdk(harness, headers)
    telephony = TelephonyClient(transport)
    key = f"call:{real_run_id}:2"

    async def create() -> Any:
        return await telephony.create_call(
            "+15555550101",
            disclosure=DISCLOSURE,
            script=SCRIPT.replace("{name}", "Asha"),
            gather_seconds=15,
            idempotency_key=key,
        )

    first = await create()
    again = await create()
    assert first.id == again.id and len(harness.twilio.calls) == 1
    final = await telephony.wait_for_call(first.id, timeout_seconds=5, poll_seconds=0.05)
    assert final.state == "completed" and final.transcript == "Yes, I can attend."

    record = await IdempotencyClient(transport).claim("row-2")
    assert (record.key, record.status) == ("row-2", "claimed")
