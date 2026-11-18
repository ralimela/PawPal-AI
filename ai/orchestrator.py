"""Orchestrator: the single entry point for the AI Assistant box.

Every request goes through the same fixed order:

    1. INPUT    empty or too long -> ask again (no AI call)
    2. SAFETY   emergency / dose question -> vet message (rules, no AI call)
    3. ROUTE    command -> Smart Add agent   (task_agent.propose_task)
                question -> Ask PawPal RAG   (care_assistant.answer_question)
                unclear  -> ask the user which one they meant

Routing uses simple word rules instead of an AI call: it's predictable, testable,
free, and saves the daily Gemini quota for the steps that need language understanding.
"""

import re
from datetime import date

from ai import reply as R
from ai.care_assistant import answer_question
from ai.reply import Reply
from ai.retriever import KnowledgeBase
from ai.safety import check_safety
from ai.task_agent import propose_task
from ai.trace import Trace
from pawpal_system import Owner

MAX_INPUT_CHARS = 500

# Words that clearly ask PawPal+ to put something on the schedule
SCHEDULE_VERBS = r"add|schedule|remind|book|set up|create|plan|put"
# Care actions that start a command like "walk Rex at 7" or "give Mia her pill"
TASK_VERBS = r"walk|feed|give|brush|groom|bathe|trim|clean|play|take"
QUESTION_STARTS = (r"how|what|when|why|which|who|where|is|are|can|could|should|"
                   r"does|do|will|would")


def classify(text: str) -> str:
    """Return 'command', 'question', or 'unclear' for a (safety-checked) request."""
    lowered = text.lower().strip()
    if re.search(rf"\b(?:{SCHEDULE_VERBS})\b", lowered):
        return "command"        # "Can you add a walk for Rex?" is still a command
    if lowered.endswith("?") or re.match(rf"(?:{QUESTION_STARTS})\b", lowered):
        return "question"
    if re.match(rf"(?:{TASK_VERBS})\b", lowered):
        return "command"
    return "unclear"


def handle(text: str, owner: Owner, kb: KnowledgeBase, client,
           today: date | None = None) -> Reply:
    """Route one request through safety and to the right AI component."""
    trace = Trace()
    text = (text or "").strip()
    trace.add("INPUT", repr(text[:120]))

    if not text:
        return Reply(R.CLARIFY, "Type a task to add (\"walk Rex every day at 7am\") "
                                "or a pet care question.", trace=trace)
    if len(text) > MAX_INPUT_CHARS:
        trace.add("INPUT", f"rejected: {len(text)} characters (limit {MAX_INPUT_CHARS})")
        return Reply(R.CLARIFY, f"That's a bit long. Please keep requests under "
                                f"{MAX_INPUT_CHARS} characters.", trace=trace)

    safety = check_safety(text)
    if safety:
        trace.add("SAFETY", f"blocked ({safety.reason}) -> no AI call")
        kind = R.EMERGENCY if safety.reason == "emergency" else R.DOSAGE
        return Reply(kind, safety.message, trace=trace)
    trace.add("SAFETY", "passed")

    route = classify(text)
    trace.add("ROUTE", route)
    if route == "command":
        return propose_task(text, owner, client, today, trace)
    if route == "question":
        return answer_question(text, owner, kb, client, trace)
    return Reply(R.CLARIFY, "Did you want to add a task or ask a question? For example: "
                            "\"Walk Rex every day at 7am\" or \"How often should I feed Mia?\"",
                 trace=trace)
