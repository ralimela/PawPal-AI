"""Tests for the safety guardrail, MockClient, and Trace. All offline."""

import pytest

from ai.llm_client import LLMError, MockClient
from ai.safety import answer_mentions_dose, check_safety
from ai.trace import Trace


@pytest.mark.parametrize("text", [
    "My dog ate chocolate, what do I do?",
    "Mia licked some lily pollen",
    "Rex swallowed ibuprofen",
    "Rex is having a seizure",
    "my cat can't breathe properly",
    "Rex was hit by a car",
    "Mia is straining to pee and nothing comes out",
])
def test_emergencies_are_blocked(text):
    result = check_safety(text)
    assert result is not None and result.reason == "emergency"


@pytest.mark.parametrize("text", [
    "How many mg of Benadryl can I give Rex?",
    "Can I give my dog ibuprofen for pain?",
    "What dose of melatonin is safe for a cat?",
])
def test_dose_questions_are_blocked(text):
    result = check_safety(text)
    assert result is not None and result.reason == "dosage"


@pytest.mark.parametrize("text", [
    "Schedule Rex's blood test on Friday at 10am",            # 'blood' alone is not an emergency
    "Give Rex his pill after he has eaten breakfast",          # pill + eaten is a normal routine
    "Give Mia her flea medicine every Monday at 2:30pm",       # medicine command, no dose question
    "Give Rex his 5 mg pill daily at 8am, the vet prescribed it",  # owner already has the vet's dose
    "How often should I feed my cat?",
    "Is chocolate bad for dogs?",                              # knowledge question, not an emergency
    "Walk Rex every day at 7am",
])
def test_normal_requests_are_not_blocked(text):
    assert check_safety(text) is None


def test_output_guardrail_spots_doses_in_answers():
    assert answer_mentions_dose("Give 25 mg twice a day.")
    assert answer_mentions_dose("about 2.5 ml")
    assert not answer_mentions_dose("Feed adult cats two meals a day.")


def test_mock_client_returns_list_replies_in_order():
    client = MockClient(["first", "second"])
    assert client.generate("a") == "first"
    assert client.generate("b") == "second"
    with pytest.raises(LLMError):
        client.generate("c")  # ran out of replies
    assert client.prompts == ["a", "b", "c"]


def test_mock_client_dict_matches_by_substring_and_can_raise():
    client = MockClient({"walk": "walk reply", "boom": LLMError("quota exceeded")})
    assert client.generate("please walk Rex") == "walk reply"
    with pytest.raises(LLMError):
        client.generate("boom")


def test_trace_records_steps_in_order():
    trace = Trace()
    trace.add("ROUTE", "command")
    trace.add("PARSE", "ok")
    assert trace.lines() == ["ROUTE: command", "PARSE: ok"]
    assert trace.has("PARSE") and not trace.has("REPAIR")
