"""Smart Add: turn a plain-English request into a verified task proposal (an agent loop).

    PARSE     Gemini turns the request into JSON: a list of 1 to 3 tasks
              (null for anything the owner didn't say).
    VALIDATE  Our code checks every field of every task. Task() itself rejects bad values.
    REPAIR    If parsing or validation fails, Gemini gets ONE retry with the exact problems.
    FALLBACK  Still invalid -> stop and point the user to the manual form.
    CHECK     Scheduler.detect_conflicts() looks for clashes: new tasks vs. the existing
              schedule AND new tasks vs. each other.
    PROPOSE   Return the tasks + assumptions + warnings. NOTHING is saved yet.
    CONFIRM   Only confirm_proposal(), called when the user clicks Confirm, adds them.
              If anything overlaps, the user must explicitly approve the overlap.

The AI never writes to the schedule directly; the existing pawpal_system code
is the guardrail, and the human is the final check. All tasks in one request
are saved together or not at all.
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
MAX_TASKS_PER_REQUEST = 3
MEDS_NOTE = ("PawPal+ doesn't check medication names or doses. "
             "Follow your vet's instructions exactly.")


@dataclass
class ProposedTask:
    """One validated task inside a proposal."""

    task: Task
    pet_name: str
    assumptions: list[str] = field(default_factory=list)  # defaults we filled in

    def summary(self) -> str:
        """One readable line, e.g. 'Morning walk for Rex: Daily from Wed Nov 18 at 07:00 AM, 30 min, High'."""
        t = self.task
        when = t.start_time.strftime("%I:%M %p") if t.start_time else "Anytime"
        start = "on" if t.frequency == "Once" else f"{t.frequency} from"
        return (f"{t.title} for {self.pet_name}: {start} {t.due_date:%a %b %d} at {when}, "
                f"{t.duration_minutes} min, {t.priority} priority ({t.category})")


@dataclass
class Proposal:
    """One or more validated tasks waiting for the user to confirm them."""

    items: list[ProposedTask]
    warnings: list[str] = field(default_factory=list)  # time conflicts the user must approve

    @property
    def needs_overlap_approval(self) -> bool:
        """True if confirming means knowingly accepting overlapping tasks."""
        return bool(self.warnings)

    def summary(self) -> str:
        """One line per task (numbered when there are several)."""
        if len(self.items) == 1:
            return self.items[0].summary()
        return "\n".join(f"{n}) {item.summary()}" for n, item in enumerate(self.items, start=1))


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
    prompt = f"""You turn a pet owner's request into tasks for the PawPal+ scheduler.

Today is {today:%A}, {today.isoformat()}.
The owner's pets: {pets}

Request: "{request}"

Reply with ONLY a JSON object of this form:
{{"tasks": [{{"pet": ..., "title": ..., "category": ..., "frequency": ..., "due_date": ...,
 "start_time": ..., "priority": ..., "duration_minutes": ...}}]}}

Rules:
- Usually the request describes one task. If it clearly describes several (for example
  "walk Rex at 7am and feed Mia at 8am"), return one object per task, in the order they
  are mentioned, at most {MAX_TASKS_PER_REQUEST}. Never split one task into several.
- A detail that the owner clearly gives for all tasks ("both every day") applies to each.
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

Fix them and reply with the corrected JSON object ({{"tasks": [...]}}) only.
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


def validate_all(data: dict, owner: Owner, today: date) -> list[tuple[Task, Pet, list[str]]]:
    """Validate every task in the reply. All of them must be valid, or none are used.

    Accepts {"tasks": [...]} and, for robustness, a single task object.
    Problems are labeled "task 2: ..." when there are several, so the repair
    prompt can tell the model exactly which task to fix.
    """
    items = data["tasks"] if isinstance(data.get("tasks"), list) else [data]
    if not items:
        raise ValidationProblem(["the tasks list is empty; return at least one task"])
    if len(items) > MAX_TASKS_PER_REQUEST:
        raise ValidationProblem([f"I can add up to {MAX_TASKS_PER_REQUEST} tasks at a time. "
                                 "Please split your request into smaller ones."], needs_user=True)

    several = len(items) > 1
    results, problems = [], []
    for number, item in enumerate(items, start=1):
        label = f"task {number}: " if several else ""
        if not isinstance(item, dict):
            problems.append(f"{label}must be a JSON object")
            continue
        try:
            results.append(validate(item, owner, today))
        except ValidationProblem as err:
            if err.needs_user:
                title = item.get("title") if isinstance(item.get("title"), str) else None
                prefix = f"Task {number} ({title}): " if several and title else (
                    f"Task {number}: " if several else "")
                raise ValidationProblem([prefix + err.problems[0]], needs_user=True) from err
            problems.extend(label + p for p in err.problems)
    if problems:
        raise ValidationProblem(problems)
    return results


# ---------------------------------------------------------------- conflicts

def find_conflicts(owner: Owner, new: list[tuple[Pet, Task]]) -> list[str]:
    """Warn about time clashes involving the new tasks.

    Checks new tasks against the existing schedule AND against each other,
    using the existing Scheduler.detect_conflicts(). Repeating tasks are
    checked over a week, since a clash may only happen on some days.
    """
    timed = [task for _, task in new if task.start_time is not None]
    if not timed:
        return []
    first = min(task.due_date for task in timed)
    last = max(task.due_date for task in timed)
    if any(task.frequency != "Once" for task in timed):
        last += timedelta(days=6)
    is_new = lambda t: any(t is task for _, task in new)

    scheduler = Scheduler(owner)
    warnings, added = [], []
    try:
        for pet, task in new:  # temporarily, so detect_conflicts() can see them
            pet.add_task(task)
            added.append((pet, task))
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            for a, b in scheduler.detect_conflicts(day):
                if is_new(a) and is_new(b):
                    warnings.append(f"{day:%a %b %d}: {a.title} ({a.pet_name}, {a.start_time:%I:%M %p}) "
                                    f"and {b.title} ({b.pet_name}, {b.start_time:%I:%M %p}) "
                                    "overlap, and both are in this request")
                elif is_new(a) or is_new(b):
                    mine, other = (a, b) if is_new(a) else (b, a)
                    prefix = "" if len(new) == 1 else f"{mine.title} ({mine.pet_name}) "
                    warnings.append(f"{day:%a %b %d}: {prefix}overlaps {other.title} "
                                    f"({other.pet_name}) at {other.start_time:%I:%M %p}")
    finally:
        for pet, task in added:  # never leave unconfirmed tasks in the schedule
            pet.remove_task(task)
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
        trace.add(step, f"attempt {attempt}: {' '.join(raw.split())[:300]}")  # one line

        try:
            validated = validate_all(parse_json(raw), owner, today)
        except ValidationProblem as err:
            if err.needs_user:
                trace.add("VALIDATE", f"needs the user: {err}")
                return Reply(R.CLARIFY, err.problems[0], trace=trace)
            problems = err.problems
        except ValueError as err:  # broken JSON
            problems = [f"the reply was not a valid JSON object ({err})"]
        else:
            defaults = sum(len(assumptions) for _, _, assumptions in validated)
            trace.add("VALIDATE", f"OK: {len(validated)} task(s); filled in defaults: {defaults}")
            warnings = find_conflicts(owner, [(pet, task) for task, pet, _ in validated])
            trace.add("CHECK", f"{len(warnings)} conflict(s)" + (f": {warnings}" if warnings else ""))
            proposal = Proposal([ProposedTask(task, pet.name, assumptions)
                                 for task, pet, assumptions in validated], warnings)
            waiting = ("waiting for the user to approve the overlap" if warnings
                       else "waiting for the user to confirm")
            trace.add("PROPOSE", f"{' | '.join(i.summary() for i in proposal.items)} -> {waiting}")
            return Reply(R.PROPOSAL, proposal.summary(), proposal=proposal, trace=trace)

        trace.add("VALIDATE", f"attempt {attempt} rejected: {'; '.join(problems)}")
        previous = raw

    trace.add("FALLBACK", f"still invalid after {MAX_ATTEMPTS} attempts -> manual form")
    return Reply(R.ERROR, "I couldn't turn that into a valid task "
                          f"({'; '.join(problems)}). Please use the manual form.", trace=trace)


def confirm_proposal(owner: Owner, proposal: Proposal, approve_overlaps: bool = False) -> list[Task]:
    """The human-in-the-loop step: add the tasks only now, after the user confirmed.

    If the tasks overlap with each other or with the schedule, the user must
    explicitly approve that (approve_overlaps=True); otherwise nothing is added.
    All tasks are added together, or none are.
    """
    if proposal.needs_overlap_approval and not approve_overlaps:
        raise ValueError("These tasks overlap. The owner must approve the overlap before adding them.")
    pets = []
    for item in proposal.items:
        pet = next((p for p in owner.pets if p.name == item.pet_name), None)
        if pet is None:
            raise ValueError(f"{item.pet_name} is no longer one of {owner.name}'s pets")
        pets.append(pet)
    for pet, item in zip(pets, proposal.items):
        pet.add_task(item.task)
    return [item.task for item in proposal.items]
