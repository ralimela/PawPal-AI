"""Smart Add: turn a plain-English request into a verified task proposal (an agent loop).

    PARSE     Gemini turns the request into JSON (null for anything not stated).
    VALIDATE  Our code checks every field. Task() itself rejects bad values.
    REPAIR    If parsing or validation fails, Gemini gets ONE retry with the exact problems.
    FALLBACK  Still invalid -> stop and point the user to the manual form.
    CHECK     Scheduler.detect_conflicts() looks for clashes with existing tasks.
    PROPOSE   Return the task + assumptions + warnings. NOTHING is saved yet.
    CONFIRM   Only confirm_proposal(), called when the user clicks Confirm, adds it.

The AI never writes to the schedule directly; the existing pawpal_system code
is the guardrail, and the human is the final check.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ai import reply as R
from ai.llm_client import LLMError
from ai.reply import Reply
from ai.trace import Trace
from pawpal_system import FREQUENCIES, PRIORITIES, Owner, Pet, Scheduler, Task

CATEGORIES = ("walk", "feeding", "meds", "grooming", "appointment", "other")
DEFAULT_MINUTES = {"walk": 30, "feeding": 10, "meds": 5, "grooming": 15,
                   "appointment": 60, "other": 15}
MAX_MINUTES = 480
MAX_ATTEMPTS = 2  # the first parse + one repair
MEDS_NOTE = ("PawPal+ doesn't check medication names or doses. "
             "Follow your vet's instructions exactly.")


@dataclass
class Proposal:
    """A validated task that is waiting for the user to confirm it."""

    task: Task
    pet_name: str
    assumptions: list[str] = field(default_factory=list)  # defaults we filled in
    warnings: list[str] = field(default_factory=list)     # schedule conflicts

    def summary(self) -> str:
        """One readable line, e.g. 'Morning walk for Rex: Daily from Wed Nov 18 at 07:00 AM, 30 min, High'."""
        t = self.task
        when = t.start_time.strftime("%I:%M %p") if t.start_time else "Anytime"
        start = "on" if t.frequency == "Once" else f"{t.frequency} from"
        return (f"{t.title} for {self.pet_name}: {start} {t.due_date:%a %b %d} at {when}, "
                f"{t.duration_minutes} min, {t.priority} priority ({t.category})")


class ValidationProblem(Exception):
    """The parsed task is not valid.

    needs_user=True means only the user can fix it (e.g. which pet?), so the
    agent asks a question instead of retrying the AI.
    """

    def __init__(self, problems: list[str], needs_user: bool = False):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.needs_user = needs_user


# ---------------------------------------------------------------- prompt

def build_parse_prompt(request: str, owner: Owner, today: date,
                       previous: str | None = None, problems: list[str] | None = None) -> str:
    """The structured-output prompt; on a repair attempt it includes the problems to fix."""
    pets = ", ".join(f"{p.name} ({p.species}, {p.age} years)" for p in owner.pets)
    pet_names = ", ".join(f'"{p.name}"' for p in owner.pets)
    prompt = f"""You turn a pet owner's request into ONE task for the PawPal+ scheduler.

Today is {today:%A}, {today.isoformat()}.
The owner's pets: {pets}

Request: "{request}"

Reply with ONLY a JSON object with exactly these keys:
{{"pet": ..., "title": ..., "category": ..., "frequency": ..., "due_date": ...,
 "start_time": ..., "priority": ..., "duration_minutes": ...}}

Rules:
- pet: exactly one of {pet_names}. Use null if the request does not clearly name one of them.
- title: a short task name without the pet's name, like "Morning walk" or "Flea medicine".
- category: one of {", ".join(CATEGORIES)}.
- frequency: "Once", "Daily", or "Weekly". "every day" means Daily; "every Monday" means Weekly.
- due_date: "YYYY-MM-DD" of the first occurrence, today or later. For "every Monday" use the
  next Monday (today if today is Monday). null if no day is given.
- start_time: 24-hour "HH:MM", or null if no time is given.
- priority: "High", "Medium", or "Low" ONLY if the owner says how important it is, else null.
- duration_minutes: a whole number ONLY if the owner says how long it takes, else null.
- Never invent details the owner did not give. Use null instead.
"""
    if problems:
        bullet_list = "\n".join(f"- {p}" for p in problems)
        prompt += f"""
Your previous reply was:
{previous}

It had these problems:
{bullet_list}

Fix them and reply with the corrected JSON object only.
"""
    return prompt


# ---------------------------------------------------------------- parse + validate

def parse_json(raw: str) -> dict:
    """Read the model's reply as a JSON object (tolerates ```json fences)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    data = json.loads(cleaned)  # raises ValueError on broken JSON
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def parse_time(value: str) -> time:
    """Accept '07:00', '7:00', '7:00 AM', or '7 AM'."""
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            return datetime.strptime(value.strip().upper(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"start_time {value!r} is not a time like 'HH:MM'")


def validate(data: dict, owner: Owner, today: date) -> tuple[Task, Pet, list[str]]:
    """Check every field and build a Task. Returns (task, pet, assumptions).

    Raises ValidationProblem listing every problem found, so a repair attempt
    can fix them all at once.
    """
    problems, assumptions = [], []

    # pet
    pet_value = data.get("pet")
    pet = next((p for p in owner.pets
                if isinstance(pet_value, str) and p.name.lower() == pet_value.strip().lower()), None)
    if pet is None:
        if pet_value in (None, "") and len(owner.pets) == 1:
            pet = owner.pets[0]
            assumptions.append(f"You have one pet, so this task is for {pet.name}.")
        elif pet_value in (None, ""):
            names = " or ".join(p.name for p in owner.pets)
            raise ValidationProblem([f"Which pet is this for: {names}?"], needs_user=True)
        else:
            problems.append(f"No pet named {pet_value!r}. Valid pets: "
                            f"{', '.join(p.name for p in owner.pets)} (or null).")

    # title
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title must be a non-empty string")
        title = ""
    title = title.strip()[:60]

    # category
    category = str(data.get("category") or "").lower()
    if category not in CATEGORIES:
        assumptions.append(f"Category {data.get('category')!r} isn't one I know, so I used 'other'.")
        category = "other"

    # frequency
    frequency = data.get("frequency")
    if frequency in (None, ""):
        frequency = "Once"
        assumptions.append("No repeat was mentioned, so it happens once.")
    elif str(frequency).capitalize() in FREQUENCIES:
        frequency = str(frequency).capitalize()
    else:
        problems.append(f"frequency must be one of {FREQUENCIES}, got {frequency!r}")

    # priority
    priority = data.get("priority")
    if priority in (None, ""):
        priority = "High" if category in ("meds", "appointment") else "Medium"
        assumptions.append(f"No priority was given, so I used {priority}"
                           + (" (medicine and appointments are important)." if priority == "High" else "."))
    elif str(priority).capitalize() in PRIORITIES:
        priority = str(priority).capitalize()
    else:
        problems.append(f"priority must be one of {PRIORITIES}, got {priority!r}")

    # due date
    due = data.get("due_date")
    if due in (None, ""):
        due_date = today
        assumptions.append("No day was given, so it starts today.")
    else:
        try:
            due_date = date.fromisoformat(str(due))
            if due_date < today:
                problems.append(f"due_date {due} is in the past (today is {today.isoformat()})")
        except ValueError:
            problems.append(f"due_date {due!r} is not a date like YYYY-MM-DD")
            due_date = today

    # start time
    start_time = None
    raw_time = data.get("start_time")
    if raw_time in (None, ""):
        assumptions.append("No time was given, so it's an 'Anytime' task.")
    else:
        try:
            start_time = parse_time(str(raw_time))
        except ValueError as err:
            problems.append(str(err))

    # duration
    minutes = data.get("duration_minutes")
    if minutes in (None, ""):
        minutes = DEFAULT_MINUTES[category]
        assumptions.append(f"No length was given, so I used {minutes} minutes "
                           f"(typical for {category}).")
    elif isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 < minutes <= MAX_MINUTES:
        problems.append(f"duration_minutes must be a whole number from 1 to {MAX_MINUTES}, got {minutes!r}")
        minutes = DEFAULT_MINUTES[category]

    if problems:
        raise ValidationProblem(problems)

    # Final check by the core model itself (same rules as the manual form)
    try:
        task = Task(title, category, minutes, priority, due_date, frequency, start_time)
    except ValueError as err:
        raise ValidationProblem([str(err)]) from err

    if category == "meds":
        assumptions.append(MEDS_NOTE)
    return task, pet, assumptions


# ---------------------------------------------------------------- conflicts

def find_conflicts(owner: Owner, pet: Pet, task: Task) -> list[str]:
    """Warn about clashes with existing tasks, checked over the next 7 days for repeating tasks."""
    if task.start_time is None:
        return []
    scheduler = Scheduler(owner)
    days = 1 if task.frequency == "Once" else 7
    warnings = []
    pet.add_task(task)  # temporarily, so the existing detect_conflicts() can see it
    try:
        for offset in range(days):
            day = task.due_date + timedelta(days=offset)
            for a, b in scheduler.detect_conflicts(day):
                if a is task or b is task:
                    other = b if a is task else a
                    warnings.append(f"{day:%a %b %d}: overlaps {other.title} ({other.pet_name}) "
                                    f"at {other.start_time:%I:%M %p}")
    finally:
        pet.remove_task(task)  # never leave the unconfirmed task in the schedule
    return list(dict.fromkeys(warnings))  # drop duplicates, keep order


# ---------------------------------------------------------------- the agent

def propose_task(request: str, owner: Owner, client, today: date | None = None,
                 trace: Trace | None = None) -> Reply:
    """Run the Smart Add agent loop. Returns a PROPOSAL, CLARIFY, or ERROR reply."""
    trace = trace or Trace()
    today = today or date.today()

    if not owner.pets:
        trace.add("CHECK", "owner has no pets yet")
        return Reply(R.CLARIFY, "Add a pet first, then I can schedule tasks for it.", trace=trace)
    if client is None:
        trace.add("FALLBACK", "offline mode (no API key): Smart Add needs Gemini")
        return Reply(R.ERROR, "Smart Add needs a Gemini API key. You can still add the task "
                              "with the manual form.", trace=trace)

    previous, problems = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        step = "PARSE" if attempt == 1 else "REPAIR"
        try:
            raw = client.generate(build_parse_prompt(request, owner, today, previous, problems),
                                  json_mode=True)
        except LLMError as err:
            trace.add(step, f"AI call failed: {err}")
            return Reply(R.ERROR, "I couldn't reach the AI right now. Please try again, or "
                                  "use the manual form.", trace=trace)
        trace.add(step, f"attempt {attempt}: {' '.join(raw.split())[:200]}")  # one line

        try:
            task, pet, assumptions = validate(parse_json(raw), owner, today)
        except ValidationProblem as err:
            if err.needs_user:
                trace.add("VALIDATE", f"needs the user: {err}")
                return Reply(R.CLARIFY, err.problems[0], trace=trace)
            problems = err.problems
        except ValueError as err:  # broken JSON
            problems = [f"the reply was not a valid JSON object ({err})"]
        else:
            trace.add("VALIDATE", f"OK; filled in defaults: {len(assumptions)}")
            warnings = find_conflicts(owner, pet, task)
            trace.add("CHECK", f"{len(warnings)} conflict(s)" + (f": {warnings}" if warnings else ""))
            proposal = Proposal(task, pet.name, assumptions, warnings)
            trace.add("PROPOSE", f"{proposal.summary()} -> waiting for the user to confirm")
            return Reply(R.PROPOSAL, proposal.summary(), proposal=proposal, trace=trace)

        trace.add("VALIDATE", f"attempt {attempt} rejected: {'; '.join(problems)}")
        previous = raw

    trace.add("FALLBACK", f"still invalid after {MAX_ATTEMPTS} attempts -> manual form")
    return Reply(R.ERROR, "I couldn't turn that into a valid task "
                          f"({'; '.join(problems)}). Please use the manual form.", trace=trace)


def confirm_proposal(owner: Owner, proposal: Proposal) -> Task:
    """The human-in-the-loop step: add the task only now, after the user clicked Confirm."""
    pet = next((p for p in owner.pets if p.name == proposal.pet_name), None)
    if pet is None:
        raise ValueError(f"{proposal.pet_name} is no longer one of {owner.name}'s pets")
    pet.add_task(proposal.task)
    return proposal.task
