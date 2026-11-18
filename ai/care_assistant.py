"""Ask PawPal: answer pet care questions from the knowledge/ guides (RAG).

Steps (each one is written to the trace):
1. PERSONALIZE: swap pet names for their species ("Mia" -> "cat") so retrieval
   finds cat guides, and collect each mentioned pet's species, age, and life stage.
2. RETRIEVE: search the guides. No evidence -> refuse WITHOUT calling the AI.
3. GENERATE: Gemini answers using ONLY the retrieved sections, citing them as [1], [2].
4. VERIFY: check the answer before showing it:
   - refusal phrase -> REFUSAL reply
   - a medication amount slipped in -> blocked (output guardrail)
   - citations must point to sections we actually sent; unknown ones are dropped
5. FALLBACK: offline or API failure -> show the best guide section directly.
"""

import re

from ai import reply as R
from ai.llm_client import LLMError
from ai.reply import Reply
from ai.retriever import KnowledgeBase
from ai.safety import DOSE_MESSAGE, answer_mentions_dose
from ai.trace import Trace
from pawpal_system import Owner, Pet

REFUSAL_TEXT = "I don't know based on my care guides."
NO_EVIDENCE_TEXT = (
    "I don't know based on my care guides. They cover everyday care for dogs and "
    "cats (feeding, exercise, grooming, vet visits, safety). For anything else, "
    "please ask your vet."
)


def life_stage(pet: Pet) -> str:
    """Return 'puppy'/'kitten', 'adult', or 'senior' using the ages in the care guides."""
    species = pet.species.lower()
    if species == "dog":
        return "puppy" if pet.age < 1 else "senior" if pet.age >= 7 else "adult"
    if species == "cat":
        return "kitten" if pet.age < 1 else "senior" if pet.age >= 10 else "adult"
    return "unknown"


def personalize(question: str, owner: Owner | None) -> tuple[str, list[str]]:
    """Replace pet names with species for retrieval, and describe each mentioned pet."""
    search_text, profiles = question, []
    for pet in owner.pets if owner else []:
        pattern = rf"\b{re.escape(pet.name)}(?:'s)?\b"
        if re.search(pattern, question, re.IGNORECASE):
            species = pet.species.lower() if pet.species.lower() in ("dog", "cat") else "pet"
            search_text = re.sub(pattern, species, search_text, flags=re.IGNORECASE)
            profiles.append(f"{pet.name}: {pet.species}, {pet.age} years old ({life_stage(pet)})")
    return search_text, profiles


def build_prompt(question: str, chunks, profiles: list[str]) -> str:
    """The grounded prompt: numbered sections, pet profiles, and strict rules."""
    sections = "\n\n".join(f"[{i}] {c.label}\n{c.text}" for i, c in enumerate(chunks, start=1))
    pets = "\n".join(profiles) if profiles else "(no specific pet mentioned)"
    return f"""You are PawPal+, a careful pet care assistant. Answer the owner's question
using ONLY the numbered guide sections below.

Guide sections:
{sections}

Pets mentioned in the question:
{pets}

Owner's question: {question}

Rules:
- Use only facts from the sections. Do not add outside knowledge.
- Cite the sections you used with their numbers in square brackets, like [1] or [2].
- If a pet is mentioned, use its species and life stage to pick the right advice
  (for example, adult cat advice for an adult cat).
- If the sections are about a different species than the one asked about, or do not
  answer the question, reply exactly: "{REFUSAL_TEXT}"
- Never give medication doses or amounts, diagnoses, or treatments. Say to ask a vet.
- Keep it short: 2 to 5 sentences, friendly and practical.
"""


def answer_question(question: str, owner: Owner | None, kb: KnowledgeBase,
                    client, trace: Trace | None = None) -> Reply:
    """Run the RAG pipeline for one question and return a Reply."""
    trace = trace or Trace()

    search_text, profiles = personalize(question, owner)
    if profiles:
        trace.add("PERSONALIZE", f"{'; '.join(profiles)} -> searching for '{search_text}'")

    chunks = kb.search(search_text)
    if not chunks:
        trace.add("RETRIEVE", "no guide section passed the evidence check -> refuse, no AI call")
        return Reply(R.REFUSAL, NO_EVIDENCE_TEXT, trace=trace)
    trace.add("RETRIEVE", ", ".join(c.label for c in chunks))

    if client is None:
        trace.add("FALLBACK", "offline mode (no API key): showing the best guide section")
        return offline_answer(chunks, trace)

    try:
        answer = client.generate(build_prompt(question, chunks, profiles))
    except LLMError as err:
        trace.add("FALLBACK", f"AI call failed ({err}); showing the best guide section")
        return offline_answer(chunks, trace)
    trace.add("GENERATE", f"{client.name} answered ({len(answer)} characters)")

    # VERIFY 1: the model's own refusal
    if REFUSAL_TEXT.lower().rstrip(".") in answer.lower():
        trace.add("VERIFY", "model said the sections don't answer it -> refusal")
        return Reply(R.REFUSAL, NO_EVIDENCE_TEXT, trace=trace)

    # VERIFY 2: output guardrail for medication amounts
    if answer_mentions_dose(answer):
        trace.add("VERIFY", "answer contained a medication amount -> blocked")
        return Reply(R.DOSAGE, DOSE_MESSAGE, trace=trace)

    # VERIFY 3: citations must refer to sections we actually provided
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)})
    valid = [n for n in cited if 1 <= n <= len(chunks)]
    if len(valid) < len(cited):
        trace.add("VERIFY", f"dropped citations to sections that don't exist: "
                            f"{sorted(set(cited) - set(valid))}")
    if not valid:
        trace.add("VERIFY", "answer cited no sections -> showing all retrieved sections as sources")
        valid = list(range(1, len(chunks) + 1))
    else:
        trace.add("VERIFY", f"citations OK: {valid}")
    sources = [f"[{n}] {chunks[n - 1].label}" for n in valid]
    return Reply(R.ANSWER, answer, sources=sources, trace=trace)


def offline_answer(chunks, trace: Trace) -> Reply:
    """Retrieval-only answer: the best matching section, quoted from the guide."""
    best = chunks[0]
    body = "\n".join(best.text.splitlines()[1:]).strip()
    message = f"Here's what my care guides say (AI summary unavailable):\n\n{body}"
    return Reply(R.ANSWER, message, sources=[f"[1] {best.label}"], trace=trace)
