from types import SimpleNamespace
from typing import Any

import pytest

from contract_probe.checks import check_isolation, ordered, summarize
from contract_probe.models import CheckResult, ProbeConfig
from crewquarters.errors import AgentError


def test_config_defaults_and_camel_case_aliases() -> None:
    config = ProbeConfig.model_validate({"knowledgeBaseId": "kb-1", "expectIsolation": False})
    assert config.knowledge_base_id == "kb-1"
    assert config.expect_isolation is False
    assert "isolation" in config.checks
    assert "cancellation" not in config.checks


def test_unknown_check_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        ProbeConfig.model_validate({"checks": ["handshake", "teleport"]})


def test_cancellation_always_runs_last() -> None:
    assert ordered(["cancellation", "events", "handshake"]) == [
        "events",
        "handshake",
        "cancellation",
    ]


async def test_isolation_is_skipped_when_not_expected() -> None:
    ctx: Any = SimpleNamespace(config=ProbeConfig(expect_isolation=False))
    status, detail = await check_isolation(ctx)
    assert status == "skipped"
    assert "expectIsolation" in detail


def test_summarize_passes_when_nothing_failed() -> None:
    results = [
        CheckResult(name="events", status="passed", detail="ok", duration_ms=1),
        CheckResult(name="isolation", status="skipped", detail="n/a", duration_ms=0),
    ]
    report = summarize(results)
    assert [c.name for c in report.checks] == ["events", "isolation"]


def test_summarize_raises_contract_checks_failed_with_the_report() -> None:
    results = [
        CheckResult(name="events", status="passed", detail="ok", duration_ms=1),
        CheckResult(name="knowledge", status="failed", detail="no passages", duration_ms=2),
    ]
    with pytest.raises(AgentError) as info:
        summarize(results)
    assert info.value.code == "CONTRACT_CHECKS_FAILED"
    assert info.value.details["checks"][1] == {
        "name": "knowledge",
        "status": "failed",
        "detail": "no passages",
        "durationMs": 2,
    }
