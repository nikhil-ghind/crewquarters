from __future__ import annotations

import random
import re

from voice_helpers import make_config

from voice_caller.config import VoiceCallerConfig
from voice_caller.prompts import build_greeting, build_system_prompt, first_name


def first_two_sentences(text: str) -> str:
    return " ".join(re.split(r"(?<=[.?!])\s+", text)[:2])


def test_greeting_discloses_automation_in_its_first_sentence(config: VoiceCallerConfig) -> None:
    greeting = build_greeting(config, "Asha Rao", random.Random(1))
    opening = re.split(r"(?<=[.?!])\s+", greeting)[0]
    assert "automated AI assistant" in opening and "Acme Dental" in opening
    assert greeting.startswith(("Hi Asha", "Hello Asha", "Hi there Asha"))
    assert greeting.endswith("?")


def test_greeting_without_a_name_still_discloses(config: VoiceCallerConfig) -> None:
    greeting = build_greeting(config, "", random.Random(2))
    assert "automated AI assistant" in first_two_sentences(greeting)


def test_greetings_vary_between_calls(config: VoiceCallerConfig) -> None:
    greetings = {build_greeting(config, "Asha", random.Random(seed)) for seed in range(12)}
    assert len(greetings) > 1


def test_system_prompt_is_honest_about_being_automated(config: VoiceCallerConfig) -> None:
    prompt = build_system_prompt(config, "Asha Rao")
    lowered = prompt.lower()
    assert "never claim to be one" in lowered and "answer honestly" in lowered
    assert "i'm a real person" not in lowered and "you are not an ai" not in lowered
    assert "automated ai assistant" in lowered


def test_system_prompt_carries_the_brief_in_order(config: VoiceCallerConfig) -> None:
    prompt = build_system_prompt(config, "Asha Rao")
    assert "confirm next week's cleaning appointments" in prompt
    first = prompt.index("1. Does your appointment time still work?")
    second = prompt.index("2. Any questions for the dentist?")
    assert first < second
    assert "You are calling Asha." in prompt


def test_system_prompt_forbids_markup_and_keeps_turns_short(config: VoiceCallerConfig) -> None:
    prompt = build_system_prompt(config, "Asha")
    assert "No brackets or stage directions" in prompt
    assert "Never exceed 25 words" in prompt
    assert "end_call" in prompt


def test_prompt_without_questions_says_so() -> None:
    prompt = build_system_prompt(make_config(questions=[], talkingPoints=[]), "")
    assert "(none; just deliver the purpose and listen)" in prompt
    assert "You are calling the person you called." in prompt


def test_first_name() -> None:
    assert first_name("  Asha Rao ") == "Asha" and first_name("") == ""
