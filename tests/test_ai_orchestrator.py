"""Tests for routing and the end-to-end orchestrator flow. All offline."""

import json
from datetime import date

import pytest

from ai import reply as R
from ai.llm_client import MockClient
from ai.orchestrator import MAX_INPUT_CHARS, classify, handle
from ai.retriever import KnowledgeBase
from ai.task_agent import confirm_proposal
from pawpal_system import Owner, Pet, Scheduler

TODAY = date(2026, 11, 17)


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase("knowledge")


@pytest.fixture
def owner():
    owner = Owner("Jordan")
    owner.add_pet(Pet("Rex", "Dog", 4))
    owner.add_pet(Pet("Mia", "Cat", 2))
    return owner


@pytest.mark.parametrize("text, route", [
    ("Walk Rex every day at 7am", "command"),
    ("Give Mia her flea medicine every Monday at 2:30pm", "command"),
    ("Add a vet appointment for Rex on Friday", "command"),
    ("Can you schedule a bath for Rex?", "command"),
    ("Remind me to brush Mia on Sundays", "command"),
    ("How often should I feed Mia?", "question"),
    ("Is chocolate bad for dogs?", "question"),
    ("lilies and cats?", "question"),
    ("Rex", "unclear"),
    ("hello there", "unclear"),
])
def test_classify_routes_requests(text, route):
    assert classify(text) == route


def test_emergency_is_blocked_before_any_ai_call(owner, kb):
    client = MockClient([])
    reply = handle("Rex ate a whole bar of chocolate!", owner, kb, client, TODAY)
    assert reply.kind == R.EMERGENCY and client.prompts == []
    assert reply.trace.lines()[-1] == "SAFETY: blocked (emergency) -> no AI call"


def test_dose_question_is_blocked(owner, kb):
    reply = handle("How many mg of Benadryl can I give Rex?", owner, kb, MockClient([]), TODAY)
    assert reply.kind == R.DOSAGE


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_INPUT_CHARS + 1)])
def test_empty_or_huge_input_asks_again(owner, kb, text):
    assert handle(text, owner, kb, MockClient([]), TODAY).kind == R.CLARIFY


def test_unclear_input_asks_which_one(owner, kb):
    assert handle("hello there", owner, kb, MockClient([]), TODAY).kind == R.CLARIFY


def test_question_goes_to_rag(owner, kb):
    client = MockClient(["Adult cats do well with two meals a day [1]."])
    reply = handle("How often should I feed Mia?", owner, kb, client, TODAY)
    assert reply.kind == R.ANSWER
    assert [s for s, _ in reply.trace.steps] == ["INPUT", "SAFETY", "ROUTE", "PERSONALIZE",
                                                  "RETRIEVE", "GENERATE", "VERIFY"]


def test_command_flows_end_to_end_into_the_real_schedule(owner, kb):
    """The whole point: an AI request ends up in the existing Scheduler, after confirmation."""
    client = MockClient([json.dumps({
        "pet": "Rex", "title": "Morning walk", "category": "walk", "frequency": "Daily",
        "due_date": "2026-11-17", "start_time": "07:00", "priority": "High",
        "duration_minutes": 30})])
    reply = handle("Walk Rex every day at 7am", owner, kb, client, TODAY)
    assert reply.kind == R.PROPOSAL
    scheduler = Scheduler(owner)
    assert scheduler.get_daily_tasks(TODAY) == []          # not saved yet

    confirm_proposal(owner, reply.proposal)                # the user clicks Confirm
    assert [t.title for t in scheduler.get_daily_tasks(TODAY)] == ["Morning walk"]
    assert [t.title for t in scheduler.get_daily_tasks(date(2026, 11, 20))] == ["Morning walk"]
