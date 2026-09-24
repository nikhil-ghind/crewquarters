import pytest

from crewquarters_contracts.profiles import family_of, is_family, resolve_profiles


def test_is_family() -> None:
    assert is_family("local.general")
    assert is_family("local.embedding")
    assert not is_family("local.general.small")
    assert not is_family("cloud.openai.gpt-small")


def test_family_of() -> None:
    assert family_of("local.general.quality") == "local.general"
    assert family_of("local.general") == "local.general"


def test_family_resolves_to_default_variant() -> None:
    assert resolve_profiles(["local.general"], None) == ["local.general.small"]


def test_family_with_model_profile_in_family() -> None:
    assert resolve_profiles(["local.general"], "local.general.quality") == ["local.general.quality"]


def test_model_profile_outside_family_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"local\.embedding\.small"):
        resolve_profiles(["local.general"], "local.embedding.small")


def test_model_profile_not_matching_exact_variant_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_profiles(["local.general.small"], "local.general.quality")


def test_exact_variant_resolves_to_itself() -> None:
    assert resolve_profiles(["local.general.quality"], None) == ["local.general.quality"]


def test_cloud_profile_resolves_to_itself_and_ignores_model_profile() -> None:
    assert resolve_profiles(["local.general", "cloud.openai.gpt-small"], "local.general.quality") == [
        "local.general.quality",
        "cloud.openai.gpt-small",
    ]


def test_empty() -> None:
    assert resolve_profiles([], None) == []
