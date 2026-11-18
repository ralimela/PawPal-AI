"""Logging and agent traces.

Every AI decision is recorded twice:
- in a Trace object, which the app shows in an "Agent trace" expander, and
- in logs/pawpal_ai.log, so failures can be reviewed after the fact.
"""

import logging
from pathlib import Path

logger = logging.getLogger("pawpal_ai")


def setup_logging(path: str = "logs/pawpal_ai.log") -> None:
    """Send pawpal_ai log lines to a file (safe to call more than once)."""
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class Trace:
    """An ordered list of (step, detail) pairs describing one request."""

    def __init__(self) -> None:
        """Start an empty trace."""
        self.steps: list[tuple[str, str]] = []

    def add(self, step: str, detail: str, level: int = logging.INFO) -> None:
        """Record one step and write it to the log file."""
        self.steps.append((step, detail))
        logger.log(level, "%s | %s", step, detail)

    def lines(self) -> list[str]:
        """Return the steps as readable lines, e.g. 'ROUTE: command'."""
        return [f"{step}: {detail}" for step, detail in self.steps]

    def has(self, step: str) -> bool:
        """Return True if a step with this name was recorded (handy in tests)."""
        return any(s == step for s, _ in self.steps)
