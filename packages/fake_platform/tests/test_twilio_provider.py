from crewquarters_fake.providers.twilio import TwilioProvider

SCRIPT = {"disclosure": "Automated call.", "text": "Hello Asha."}
GATHER = {"input": "speech", "timeoutSeconds": 10}


def provider() -> TwilioProvider:
    twilio = TwilioProvider()
    twilio.load(
        {
            "+15555550101": {"status": "completed", "speech": "Yes, I will attend"},
            "+15555550102": {"status": "completed"},
            "+15555550103": {"status": "busy"},
            "+15555550104": {"status": "no-answer"},
            "+15555550105": {"status": "failed"},
        }
    )
    return twilio


def poll_to_end(twilio: TwilioProvider, call_id: str) -> list[str]:
    states = []
    for _ in range(10):
        call = twilio.get(call_id)
        states.append(call["state"])
        if call["state"] in {"completed", "busy", "no-answer", "failed", "canceled"}:
            break
    return states


def test_answered_call_with_speech_progresses_one_state_per_poll() -> None:
    twilio = provider()
    call = twilio.create("run-1", "+15555550101", SCRIPT, GATHER, "call:run-1:2")
    assert call["state"] == "queued"
    assert call["toMasked"] == "••••0101"
    assert call["id"].startswith("CA")
    assert poll_to_end(twilio, call["id"]) == ["ringing", "in-progress", "completed"]
    final = twilio.get(call["id"])
    assert final["answered"] is True
    assert final["speechCaptured"] is True
    assert final["transcript"] == "Yes, I will attend"
    assert final["durationSeconds"] is not None


def test_answered_without_speech() -> None:
    twilio = provider()
    call = twilio.create("run-1", "+15555550102", SCRIPT, GATHER, "k")
    poll_to_end(twilio, call["id"])
    final = twilio.get(call["id"])
    assert (final["answered"], final["speechCaptured"], final["transcript"]) == (True, False, None)


def test_busy_no_answer_and_failed() -> None:
    twilio = provider()
    for number, expected in [
        ("+15555550103", "busy"),
        ("+15555550104", "no-answer"),
        ("+15555550105", "failed"),
    ]:
        call = twilio.create("run-1", number, SCRIPT, GATHER, f"k{number}")
        assert poll_to_end(twilio, call["id"])[-1] == expected
        assert twilio.get(call["id"])["answered"] is False


def test_unverified_number_fails() -> None:
    twilio = provider()
    call = twilio.create("run-1", "+15555559999", SCRIPT, GATHER, "k")
    poll_to_end(twilio, call["id"])
    final = twilio.get(call["id"])
    assert (final["state"], final["errorCode"]) == ("failed", "unverified-number")


def test_same_idempotency_key_places_one_call() -> None:
    twilio = provider()
    a = twilio.create("run-1", "+15555550101", SCRIPT, GATHER, "call:run-1:2")
    b = twilio.create("run-1", "+15555550101", SCRIPT, GATHER, "call:run-1:2")
    c = twilio.create("run-2", "+15555550101", SCRIPT, GATHER, "call:run-1:2")
    assert a["id"] == b["id"] != c["id"]
    assert twilio.calls_by_number() == {"+15555550101": 2}


def test_ring_polls_keeps_a_call_ringing() -> None:
    twilio = TwilioProvider()
    twilio.load({"+15555550301": {"status": "completed", "ringPolls": 3}})
    call = twilio.create("run-1", "+15555550301", SCRIPT, GATHER, "k")
    assert poll_to_end(twilio, call["id"]) == [
        "ringing",
        "ringing",
        "ringing",
        "in-progress",
        "completed",
    ]
