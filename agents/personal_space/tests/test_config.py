import pytest
from pydantic import ValidationError

from personal_space.config import PersonalSpaceConfig


def test_defaults() -> None:
    config = PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1"})
    assert config.intent is None
    assert config.ask_for_intent is True
    assert config.intent_wait_seconds == 300
    assert config.timezone == "UTC"
    assert config.model_profile == "local.general.small"
    assert (config.top_k, config.max_queries, config.max_passages) == (6, 12, 40)
    assert config.min_relevance == 0.3
    assert config.min_evidence_passages == 3


def test_a_knowledge_base_is_required() -> None:
    with pytest.raises(ValidationError):
        PersonalSpaceConfig.model_validate({})
    with pytest.raises(ValidationError):
        PersonalSpaceConfig.model_validate({"knowledgeBaseId": ""})


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_intent_means_no_intent(blank: str) -> None:
    config = PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", "intent": blank})
    assert config.intent is None


def test_intent_is_trimmed_and_bounded() -> None:
    config = PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", "intent": "  plan  "})
    assert config.intent == "plan"
    with pytest.raises(ValidationError):
        PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", "intent": "x" * 501})


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "UTC", "America/New_York"])
def test_iana_timezones_are_accepted(zone: str) -> None:
    assert (
        PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", "timezone": zone}).timezone
        == zone
    )


@pytest.mark.parametrize("zone", ["IST", "EST5EDT", "Mars/Olympus", ""])
def test_abbreviations_and_unknown_zones_are_rejected(zone: str) -> None:
    with pytest.raises(ValidationError) as info:
        PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", "timezone": zone})
    assert "IANA" in str(info.value)


@pytest.mark.parametrize(
    "field,value",
    [("topK", 0), ("topK", 21), ("maxQueries", 1), ("maxPassages", 4), ("minEvidencePassages", 0)],
)
def test_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        PersonalSpaceConfig.model_validate({"knowledgeBaseId": "kb-1", field: value})
