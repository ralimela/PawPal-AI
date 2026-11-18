"""The single result type every AI path returns, so the UI and CLI treat them the same."""

from dataclasses import dataclass, field

from ai.trace import Trace

# Every kind of reply the assistant can give
ANSWER = "answer"          # grounded answer from the care guides
REFUSAL = "refusal"        # the guides don't cover it
EMERGENCY = "emergency"    # safety guardrail: go to a vet now
DOSAGE = "dosage"          # safety guardrail: no medication doses
PROPOSAL = "proposal"      # Smart Add built a task waiting for the user's OK
CLARIFY = "clarify"        # need more information from the user
ERROR = "error"            # something failed; handled safely


@dataclass
class Reply:
    """What the assistant says back, plus everything needed to explain it."""

    kind: str
    message: str
    sources: list[str] = field(default_factory=list)   # citation labels for answers
    proposal: object | None = None                     # a task_agent.Proposal for PROPOSAL replies
    trace: Trace = field(default_factory=Trace)
