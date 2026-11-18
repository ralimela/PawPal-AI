"""End-to-end demo of the PawPal+ AI Assistant in the terminal.

    python ai_demo.py          # uses Gemini if GEMINI_API_KEY is set
    python ai_demo.py --mock   # offline: scripted model replies, no API key needed

It builds a household (Rex the dog, Mia the cat, one vet visit already booked),
sends 9 requests through the same orchestrator the Streamlit app uses, confirms
each task proposal the way a user would, and prints the final schedule.
"""

import argparse
import json
from datetime import date, time, timedelta

from ai import reply as R
from ai.llm_client import MockClient, get_client
from ai.orchestrator import handle
from ai.retriever import KnowledgeBase
from ai.task_agent import confirm_proposal
from ai.trace import setup_logging
from main import print_tasks
from pawpal_system import Owner, Pet, Scheduler, Task

REQUESTS = [
    "Walk Rex every day at 7am",
    "Give Mia her flea medicine every Monday at 2:30pm",
    "Feed Rex and brush Mia at 6pm today",
    "add a bath at 5pm",
    "How often should I feed Mia?",
    "Can dogs eat grapes?",
    "What's the best pizza place in town?",
    "Rex just ate a whole bar of chocolate!",
    "How many mg of Benadryl can I give Rex?",
]


def next_monday(today: date) -> date:
    """Today if it's Monday, otherwise the coming Monday."""
    return today + timedelta(days=(7 - today.weekday()) % 7)


def build_household(today: date) -> Owner:
    """Rex and Mia, with Mia's vet checkup already booked for Monday at 2 PM."""
    owner = Owner("Jordan")
    rex, mia = Pet("Rex", "Dog", 4), Pet("Mia", "Cat", 2)
    owner.add_pet(rex)
    owner.add_pet(mia)
    mia.add_task(Task("Vet checkup", "appointment", 60, "High", next_monday(today),
                      start_time=time(14, 0)))
    return owner


def mock_client(today: date) -> MockClient:
    """Scripted 'Gemini' replies, matched by the request text inside each prompt."""
    def task(**fields):
        base = dict(pet=None, title=None, category=None, frequency=None, due_date=None,
                    start_time=None, priority=None, duration_minutes=None)
        base.update(fields)
        return json.dumps(base)

    return MockClient({
        REQUESTS[0]: task(pet="Rex", title="Morning walk", category="walk", frequency="Daily",
                          due_date=today.isoformat(), start_time="07:00"),
        REQUESTS[1]: task(pet="Mia", title="Flea medicine", category="meds", frequency="Weekly",
                          due_date=next_monday(today).isoformat(), start_time="14:30"),
        REQUESTS[2]: json.dumps({"tasks": [
            json.loads(task(pet="Rex", title="Dinner", category="feeding",
                            due_date=today.isoformat(), start_time="18:00")),
            json.loads(task(pet="Mia", title="Brushing", category="grooming",
                            due_date=today.isoformat(), start_time="18:00"))]}),
        REQUESTS[3]: task(title="Bath", category="grooming", start_time="17:00"),
        REQUESTS[4]: "Since Mia is an adult cat, feed her at least two meals a day at regular "
                     "times; several small meals spread through the day also work well [1]. "
                     "Keep fresh water available, and ask your vet for the right amount [1].",
        REQUESTS[5]: "No. Grapes and raisins can cause kidney failure in dogs, so keep them "
                     "away from Rex [1]. If a dog eats some, contact your vet or an animal "
                     "poison control hotline right away [1].",
    })


def show(reply) -> None:
    """Print one reply the way the app shows it."""
    message = reply.message.replace("\n", "\n" + " " * 14)  # indent extra task lines
    print(f"  -> {reply.kind.upper()}: {message}")
    for source in reply.sources:
        print(f"     source {source}")
    if reply.proposal:
        for item in reply.proposal.items:
            for note in item.assumptions:
                print(f"     assumed ({item.task.title}): {note}")
        for warning in reply.proposal.warnings:
            print(f"     WARNING (overlap): {warning}")
    print("     trace: " + " | ".join(reply.trace.lines()))


def main() -> None:
    """Run every demo request, confirm proposals, and print the resulting schedule."""
    parser = argparse.ArgumentParser(description="PawPal+ AI Assistant demo")
    parser.add_argument("--mock", action="store_true", help="use scripted replies (offline)")
    args = parser.parse_args()

    setup_logging()
    today = date.today()
    client = mock_client(today) if args.mock else get_client()
    mode = client.name if client else "offline (no GEMINI_API_KEY; try --mock)"
    owner = build_household(today)
    kb = KnowledgeBase("knowledge")

    print(f"PawPal+ AI Assistant demo - model: {mode}")
    print("=" * 70)
    for number, request in enumerate(REQUESTS, start=1):
        print(f'\n[{number}] "{request}"')
        reply = handle(request, owner, kb, client, today)
        show(reply)
        if reply.kind == R.PROPOSAL:
            approve = reply.proposal.needs_overlap_approval
            added = confirm_proposal(owner, reply.proposal, approve_overlaps=approve)
            action = 'clicked "Yes, add anyway" (overlap approved)' if approve else "clicked Confirm"
            print(f"     user {action} -> {len(added)} task(s) added to the schedule")

    print_tasks(f"Today's schedule ({today:%a %b %d}) after the assistant's changes",
                Scheduler(owner).get_daily_tasks(today), today)
    for warning in Scheduler(owner).get_conflict_warnings(today):
        print(f"  WARNING: {warning}")

    monday = next_monday(today)
    print_tasks(f"Schedule for Monday {monday:%b %d} after the assistant's changes",
                Scheduler(owner).get_daily_tasks(monday), monday)
    for warning in Scheduler(owner).get_conflict_warnings(monday):
        print(f"  WARNING: {warning}")


if __name__ == "__main__":
    main()
