"""Tests for the Smart Add agent. All offline: MockClient plays the part of Gemini."""

import json
from datetime import date, time

import pytest

from ai import reply as R
from ai.llm_client import LLMError, MockClient
from ai.task_agent import MEDS_NOTE, confirm_proposal, parse_json, parse_time, propose_task
from pawpal_system import Owner, Pet, Task

TODAY = date(2026, 11, 17)  # a Tuesday


def js(**fields) -> str:
    """Build a model reply with all keys present (null unless given)."""
    base = dict(pet=None, title=None, category=None, frequency=None, due_date=None,
                start_time=None, priority=None, duration_minutes=None)
    base.update(fields)
    return json.dumps(base)


@pytest.fixture
def owner():
    owner = Owner("Jordan")
    owner.add_pet(Pet("Rex", "Dog", 4))
    owner.add_pet(Pet("Mia", "Cat", 2))
    return owner


def total_tasks(owner):
    return len(owner.get_all_tasks())


# ---------- happy path + human-in-the-loop ----------

def test_full_request_becomes_a_proposal_but_is_not_saved(owner):
    client = MockClient([js(pet="Rex", title="Morning walk", category="walk", frequency="Daily",
                            due_date="2026-11-18", start_time="07:00", priority="High",
                            duration_minutes=30)])
    reply = propose_task("Walk Rex every day at 7am starting tomorrow, high priority, 30 min",
                         owner, client, TODAY)
    assert reply.kind == R.PROPOSAL
    task = reply.proposal.task
    assert (task.title, task.frequency, task.start_time, task.priority) == \
           ("Morning walk", "Daily", time(7, 0), "High")
    assert reply.proposal.assumptions == []
    assert total_tasks(owner) == 0  # nothing saved until the user confirms


def test_confirm_adds_the_task_to_the_right_pet(owner):
    client = MockClient([js(pet="Mia", title="Breakfast", category="feeding", frequency="Daily",
                            due_date="2026-11-17", start_time="08:00")])
    reply = propose_task("feed Mia breakfast every day at 8", owner, client, TODAY)
    confirm_proposal(owner, reply.proposal)
    mia = owner.pets[1]
    assert [t.title for t in mia.tasks] == ["Breakfast"]
    assert mia.tasks[0].pet_name == "Mia"


def test_missing_fields_get_defaults_and_each_is_listed_as_an_assumption(owner):
    client = MockClient([js(pet="Rex", title="Brush coat", category="grooming")])
    reply = propose_task("brush Rex", owner, client, TODAY)
    task = reply.proposal.task
    assert (task.frequency, task.priority, task.due_date, task.start_time, task.duration_minutes) == \
           ("Once", "Medium", TODAY, None, 15)
    assert len(reply.proposal.assumptions) == 5  # repeat, priority, day, time, length


def test_meds_default_to_high_priority_with_a_dose_note(owner):
    client = MockClient([js(pet="Mia", title="Flea medicine", category="meds", frequency="Weekly",
                            due_date="2026-11-23", start_time="14:30")])
    reply = propose_task("Give Mia her flea medicine every Monday at 2:30pm", owner, client, TODAY)
    assert reply.proposal.task.priority == "High"
    assert MEDS_NOTE in reply.proposal.assumptions


# ---------- repair loop and fallback ----------

def test_invalid_value_is_repaired_on_the_second_attempt(owner):
    client = MockClient([
        js(pet="Rex", title="Walk", category="walk", frequency="Every day"),   # bad frequency
        js(pet="Rex", title="Walk", category="walk", frequency="Daily"),
    ])
    reply = propose_task("walk Rex every day", owner, client, TODAY)
    assert reply.kind == R.PROPOSAL and reply.proposal.task.frequency == "Daily"
    assert reply.trace.has("REPAIR")
    # The repair prompt told the model exactly what was wrong
    assert "frequency must be one of" in client.prompts[1]


def test_broken_json_is_repaired(owner):
    client = MockClient(["Sure! Here is your task: walk Rex",
                         js(pet="Rex", title="Walk", category="walk")])
    reply = propose_task("walk Rex", owner, client, TODAY)
    assert reply.kind == R.PROPOSAL


def test_gives_up_after_two_bad_attempts_and_saves_nothing(owner):
    client = MockClient(["not json", "still not json"])
    reply = propose_task("walk Rex", owner, client, TODAY)
    assert reply.kind == R.ERROR and "manual form" in reply.message
    assert reply.trace.has("FALLBACK")
    assert len(client.prompts) == 2  # never more than one repair
    assert total_tasks(owner) == 0


def test_past_date_is_rejected(owner):
    client = MockClient([js(pet="Rex", title="Walk", category="walk", due_date="2026-11-01"),
                         js(pet="Rex", title="Walk", category="walk", due_date="2026-11-01")])
    reply = propose_task("walk Rex on Nov 1", owner, client, TODAY)
    assert reply.kind == R.ERROR and "in the past" in reply.message


def test_unknown_pet_name_is_rejected_not_guessed(owner):
    client = MockClient([js(pet="Max", title="Walk", category="walk"),
                         js(pet="Max", title="Walk", category="walk")])
    reply = propose_task("walk Max", owner, client, TODAY)
    assert reply.kind == R.ERROR and "No pet named 'Max'" in reply.message


# ---------- asking the user instead of guessing ----------

def test_no_pet_named_with_two_pets_asks_which_one(owner):
    client = MockClient([js(title="Walk", category="walk")])
    reply = propose_task("add a walk at 7am", owner, client, TODAY)
    assert reply.kind == R.CLARIFY and "Rex or Mia" in reply.message
    assert len(client.prompts) == 1  # no pointless retry


def test_single_pet_is_assumed_and_says_so():
    owner = Owner("Sam")
    owner.add_pet(Pet("Rex", "Dog", 4))
    reply = propose_task("add a walk", owner, MockClient([js(title="Walk", category="walk")]), TODAY)
    assert reply.kind == R.PROPOSAL
    assert "You have one pet" in reply.proposal.assumptions[0]


def test_owner_without_pets_is_told_to_add_one():
    reply = propose_task("walk Rex", Owner("Sam"), MockClient([]), TODAY)
    assert reply.kind == R.CLARIFY


# ---------- verification against the real schedule ----------

def test_conflict_with_existing_task_is_warned_and_not_saved(owner):
    owner.pets[1].add_task(Task("Vet checkup", "appointment", 60, "Medium", TODAY,
                                start_time=time(14, 0)))
    client = MockClient([js(pet="Mia", title="Flea medicine", category="meds",
                            due_date="2026-11-17", start_time="14:30")])
    reply = propose_task("flea medicine for Mia today at 2:30pm", owner, client, TODAY)
    assert reply.proposal.warnings == ["Tue Nov 17: overlaps Vet checkup (Mia) at 02:00 PM"]
    assert total_tasks(owner) == 1  # the checkup only; the proposal was removed again


def test_daily_task_is_checked_against_the_whole_week(owner):
    owner.pets[0].add_task(Task("Vet visit", "appointment", 60, "High", date(2026, 11, 20),
                                start_time=time(7, 0)))
    client = MockClient([js(pet="Rex", title="Walk", category="walk", frequency="Daily",
                            due_date="2026-11-17", start_time="07:00")])
    reply = propose_task("walk Rex daily at 7", owner, client, TODAY)
    assert reply.proposal.warnings == ["Fri Nov 20: overlaps Vet visit (Rex) at 07:00 AM"]


# ---------- failures and helpers ----------

def test_api_failure_is_handled_safely(owner):
    reply = propose_task("walk Rex", owner, MockClient([LLMError("quota exceeded")]), TODAY)
    assert reply.kind == R.ERROR and total_tasks(owner) == 0


def test_offline_mode_points_to_the_manual_form(owner):
    reply = propose_task("walk Rex", owner, None, TODAY)
    assert reply.kind == R.ERROR and "manual form" in reply.message


def test_parse_helpers():
    assert parse_json('```json\n{"pet": "Rex"}\n```') == {"pet": "Rex"}
    with pytest.raises(ValueError):
        parse_json("[1, 2]")
    assert parse_time("7 AM") == parse_time("07:00") == time(7, 0)
    assert parse_time("2:30 pm") == time(14, 30)
