"""Safety guardrail: rule-based, runs BEFORE any AI call.

PawPal+ is a planner, not a vet. Two kinds of input are blocked:
1. Emergencies (a pet ate something toxic, is bleeding, can't breathe, ...):
   the owner needs a vet now, not a chatbot answer.
2. Medication-dose questions ("how many mg of Benadryl can I give?"):
   a wrong dose can kill a pet, so PawPal never answers them.

Rules instead of AI: they are predictable, free, testable, and work offline.
"""

import re
from dataclasses import dataclass

EMERGENCY_MESSAGE = (
    "This sounds like it could be an emergency. Please contact your vet or an "
    "emergency animal hospital right away. If your pet may have eaten something "
    "toxic, an animal poison control hotline can also help. PawPal+ can't give "
    "emergency or medical advice."
)
DOSE_MESSAGE = (
    "PawPal+ can't advise on medication doses or which human medicines are safe. "
    "Some common human medicines are dangerous for pets. Please ask your vet; "
    "once they give you a dose, PawPal+ can schedule it for you."
)

# Things that are dangerous if eaten (used together with an "ate" word).
TOXINS = (
    r"chocolate|grapes?|raisins?|onions?|garlic|xylitol|gum|alcohol|beer|wine|coffee|"
    r"caffeine|macadamia|lil(?:y|ies)|antifreeze|rat poison|poison|bleach|"
    r"ibuprofen|advil|tylenol|acetaminophen|aspirin|"
    r"something toxic|something poisonous"
)
# Generic words like "pill" or "medicine" are left out on purpose:
# "give Rex his pill after he has eaten" is a normal command, not an emergency.
ATE = r"ate|eaten|eating|swallowed|licked|chewed|got into|drank|ingested"

# Symptoms that need a vet now, no matter how they are phrased.
EMERGENCY_SYMPTOMS = (
    r"not breathing|can'?t breathe|trouble breathing|difficulty breathing|choking|"
    r"seizures?|seizing|collapsed?|unconscious|unresponsive|bleeding|"
    r"vomiting blood|throwing up blood|blood in (?:his |her |their )?(?:stool|poop|urine|pee)|"
    r"hit by a car|poisoned|heatstroke|can'?t stand|can'?t walk|"
    r"bloated|swollen belly|can'?t pee|can'?t urinate|straining to pee|emergency"
)

MEDICINES = (
    r"medicine|medication|meds|pills?|tablets?|benadryl|ibuprofen|advil|tylenol|"
    r"acetaminophen|aspirin|antibiotics?|painkillers?|pain relievers?|melatonin|"
    r"pepto|imodium|dewormer|antihistamines?"
)
# Asking for advice about an amount, or whether a medicine is OK to give.
DOSE_ASK = (
    r"how much|how many|what dose|what dosage|dose|dosage|\d+\s*mg|milligrams?|"
    r"can i give|should i give|is it safe to give|is it ok to give|okay to give"
)


@dataclass
class SafetyResult:
    """Why an input was blocked and what to tell the owner."""

    reason: str   # "emergency" or "dosage"
    message: str


def _has(pattern: str, text: str) -> bool:
    """Return True if the regex pattern matches text as whole words."""
    return re.search(rf"\b(?:{pattern})\b", text) is not None


def check_safety(text: str) -> SafetyResult | None:
    """Return a SafetyResult if the input must be blocked, otherwise None."""
    lowered = text.lower()
    if _has(EMERGENCY_SYMPTOMS, lowered) or (_has(ATE, lowered) and _has(TOXINS, lowered)):
        return SafetyResult("emergency", EMERGENCY_MESSAGE)
    if _has(MEDICINES, lowered) and _has(DOSE_ASK, lowered) and looks_like_advice_request(lowered):
        return SafetyResult("dosage", DOSE_MESSAGE)
    return None


def looks_like_advice_request(lowered: str) -> bool:
    """True if the text asks for advice ('can I give...?'), not a schedule command.

    'Give Rex his pill (5 mg, prescribed) daily at 8am' is a command: the owner
    already has the vet's dose and just wants a reminder. That's allowed.
    'How many mg of Benadryl can I give Rex?' is asking PawPal for a dose: blocked.
    """
    asks = re.search(r"\b(how|what|can i|should i|is it|is .* (safe|ok|okay))\b", lowered)
    return lowered.rstrip().endswith("?") or asks is not None


# Output guardrail: catches a dose that slipped into a model's answer anyway.
DOSE_IN_ANSWER = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|milligrams?|ml|milliliters?|cc)\b", re.I)


def answer_mentions_dose(answer: str) -> bool:
    """Return True if a generated answer contains a specific amount like '25 mg'."""
    return DOSE_IN_ANSWER.search(answer) is not None
