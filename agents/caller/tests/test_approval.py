from caller_agent.approval import approval_key, build_request, personalised
from caller_agent.config import CallerConfig
from caller_agent.rows import Plan, classify, read_rows


def plan(numbers: list[str]) -> Plan:
    values = [[f"P{chr(ord('a') + i)}", number, "yes", ""] for i, number in enumerate(numbers)]
    values.append(["Skip", "+15555550199", "no", ""])
    return classify(read_rows(values, 2), 3)


CONFIG = CallerConfig.model_validate({"spreadsheetId": "s1"})


def test_key_is_stable_and_changes_with_script_or_recipients() -> None:
    base = approval_key(plan(["+15555550101", "+15555550102"]), "Hello {name}", "Automated call.")
    assert base == approval_key(
        plan(["+15555550101", "+15555550102"]), "Hello {name}", "Automated call."
    )
    assert base.startswith("confirm-calls-v1:") and len(base) == len("confirm-calls-v1:") + 16
    assert base != approval_key(
        plan(["+15555550101", "+15555550103"]), "Hello {name}", "Automated call."
    )
    assert base != approval_key(
        plan(["+15555550101", "+15555550102"]), "Hi {name}", "Automated call."
    )
    assert base != approval_key(
        plan(["+15555550101", "+15555550102"]), "Hello {name}", "Different disclosure."
    )


def test_request_shows_masked_recipients_script_skips_and_count() -> None:
    request = build_request(plan(["+15555550101", "+15555550102", "+15555550103"]), CONFIG)
    assert request["title"] == "Approve 3 automated calls"
    assert [(c.value, c.label, c.style) for c in request["choices"]] == [
        ("approve", "Approve 3 calls", "primary"),
        ("cancel", "Cancel run", "secondary"),
    ]
    assert request["consequence"].startswith("3 automated calls will be placed now")
    recipients, script, skipped, summary = request["preview"]
    assert recipients["columns"] == ["Row", "Name", "Number", "Consent"]
    assert recipients["rows"][0] == ["2", "Pa", "••••0101", "validated"]
    assert CONFIG.disclosure in script["text"] and CONFIG.script in script["text"]
    assert skipped["rows"] == [["5", "Skip", "consent"]]
    assert {"label": "Call cap", "value": "3"} in summary["items"]
    rendered = repr(request)
    for number in ("+15555550101", "+15555550102", "+15555550103", "+15555550199"):
        assert number not in rendered


def test_single_call_label_is_singular() -> None:
    request = build_request(plan(["+15555550101"]), CONFIG)
    assert request["choices"][0].label == "Approve 1 call"
    assert request["title"] == "Approve 1 automated call"


def test_personalised_only_substitutes_the_name() -> None:
    assert personalised("Hello {name}.", "Asha {evil}") == "Hello Asha {evil}."
