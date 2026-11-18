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
    task = reply.proposal.items[0].task
    assert (task.title, task.frequency, task.start_time, task.priority) == \
           ("Morning walk", "Daily", time(7, 0), "High")
    assert reply.proposal.items[0].assumptions == []
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
    task = reply.proposal.items[0].task
    assert (task.frequency, task.priority, task.due_date, task.start_time, task.duration_minutes) == \
           ("Once", "Medium", TODAY, None, 15)
    assert len(reply.proposal.items[0].assumptions) == 5  # repeat, priority, day, time, length


def test_meds_default_to_high_priority_with_a_dose_note(owner):
    client = MockClient([js(pet="Mia", title="Flea medicine", category="meds", frequency="Weekly",
                            due_date="2026-11-23", start_time="14:30")])
    reply = propose_task("Give Mia her flea medicine every Monday at 2:30pm", owner, client, TODAY)
    assert reply.proposal.items[0].task.priority == "High"
    assert MEDS_NOTE in reply.proposal.items[0].assumptions


# ---------- repair loop and fallback ----------

def test_invalid_value_is_repaired_on_the_second_attempt(owner):
    client = MockClient([
        js(pet="Rex", title="Walk", category="walk", frequency="Every day"),   # bad frequency
        js(pet="Rex", title="Walk", category="walk", frequency="Daily"),
    ])
    reply = propose_task("walk Rex every day", owner, client, TODAY)
    assert reply.kind == R.PROPOSAL and reply.proposal.items[0].task.frequency == "Daily"
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
    assert "You have one pet" in reply.proposal.items[0].assumptions[0]


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


# ---------- several tasks in one request ----------

def tasks_reply(*tasks) -> str:
    """A model reply in the {"tasks": [...]} format."""
    return json.dumps({"tasks": [json.loads(t) for t in tasks]})


def test_two_tasks_in_one_request_become_one_proposal(owner):
    client = MockClient([tasks_reply(
        js(pet="Rex", title="Walk", category="walk", start_time="07:00"),
        js(pet="Mia", title="Breakfast", category="feeding", start_time="08:00"))])
    reply = propose_task("walk Rex at 7am and feed Mia at 8am", owner, client, TODAY)
    assert reply.kind == R.PROPOSAL
    assert [i.pet_name for i in reply.proposal.items] == ["Rex", "Mia"]
    assert reply.proposal.warnings == [] and not reply.proposal.needs_overlap_approval
    confirm_proposal(owner, reply.proposal)
    assert sorted(t.title for t in owner.get_all_tasks()) == ["Breakfast", "Walk"]


def test_overlapping_tasks_in_one_request_are_flagged(owner):
    client = MockClient([tasks_reply(
        js(pet="Rex", title="Walk", category="walk", start_time="07:00"),
        js(pet="Mia", title="Breakfast", category="feeding", start_time="07:00"))])
    reply = propose_task("walk Rex and feed Mia at 7am", owner, client, TODAY)
    assert reply.proposal.needs_overlap_approval
    assert reply.proposal.warnings == [
        "Tue Nov 17: Walk (Rex, 07:00 AM) and Breakfast (Mia, 07:00 AM) overlap, "
        "and both are in this request"]
    assert total_tasks(owner) == 0  # the check removed both temporary tasks again


def test_overlap_cannot_be_confirmed_without_explicit_approval(owner):
    client = MockClient([tasks_reply(
        js(pet="Rex", title="Walk", category="walk", start_time="07:00"),
        js(pet="Mia", title="Breakfast", category="feeding", start_time="07:00"))])
    proposal = propose_task("walk Rex and feed Mia at 7am", owner, client, TODAY).proposal
    with pytest.raises(ValueError, match="approve the overlap"):
        confirm_proposal(owner, proposal)
    assert total_tasks(owner) == 0

    confirm_proposal(owner, proposal, approve_overlaps=True)  # the owner clicked "Yes, add anyway"
    assert total_tasks(owner) == 2


def test_overlap_with_existing_schedule_also_needs_approval(owner):
    owner.pets[1].add_task(Task("Vet checkup", "appointment", 60, "Medium", TODAY,
                                start_time=time(14, 0)))
    client = MockClient([js(pet="Mia", title="Flea medicine", category="meds",
                            due_date="2026-11-17", start_time="14:30")])
    proposal = propose_task("flea medicine for Mia at 2:30pm", owner, client, TODAY).proposal
    with pytest.raises(ValueError):
        confirm_proposal(owner, proposal)


def test_one_bad_task_means_nothing_is_saved(owner):
    bad = tasks_reply(js(pet="Rex", title="Walk", category="walk"),
                      js(pet="Mia", title="Breakfast", category="feeding", frequency="sometimes"))
    reply = propose_task("walk Rex and feed Mia", owner, MockClient([bad, bad]), TODAY)
    assert reply.kind == R.ERROR and "task 2: frequency" in reply.message
    assert total_tasks(owner) == 0


def test_bad_second_task_is_repaired_with_a_labeled_problem(owner):
    bad = tasks_reply(js(pet="Rex", title="Walk", category="walk"),
                      js(pet="Mia", title="Breakfast", category="feeding", frequency="sometimes"))
    good = tasks_reply(js(pet="Rex", title="Walk", category="walk"),
                       js(pet="Mia", title="Breakfast", category="feeding", frequency="Daily"))
    client = MockClient([bad, good])
    reply = propose_task("walk Rex and feed Mia daily", owner, client, TODAY)
    assert reply.kind == R.PROPOSAL and len(reply.proposal.items) == 2
    assert "task 2: frequency must be one of" in client.prompts[1]


def test_missing_pet_in_second_task_asks_about_that_task(owner):
    client = MockClient([tasks_reply(js(pet="Rex", title="Walk", category="walk"),
                                     js(title="Bath", category="grooming"))])
    reply = propose_task("walk Rex and give a bath", owner, client, TODAY)
    assert reply.kind == R.CLARIFY
    assert reply.message.startswith("Task 2 (Bath): Which pet") and "Rex or Mia" in reply.message


def test_more_than_three_tasks_asks_to_split(owner):
    four = tasks_reply(*[js(pet="Rex", title=f"Task {n}", category="other") for n in range(4)])
    reply = propose_task("four things for Rex", owner, MockClient([four]), TODAY)
    assert reply.kind == R.CLARIFY and "up to 3 tasks" in reply.message
