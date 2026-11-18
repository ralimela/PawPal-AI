"""Reliability evaluation for the PawPal+ AI Assistant.

    python evaluate.py           # live: real Gemini calls (about 12 requests)
    python evaluate.py --mock    # offline: scripted model replies

Each case in eval_cases.json says what SHOULD happen (the reply kind, and for
tasks the pet/frequency/time/category; for answers the guide it must cite).
Every case runs on a fresh household through the same orchestrator as the app,
and the results are printed and saved to eval_results.md as a markdown table.

--mock checks the deterministic parts (safety, routing, retrieval, validation,
conflict checks) with "ideal" model replies. Live mode measures the real model.
"""

import argparse
import json
import sys
import time as clock
from datetime import date, time, timedelta

from ai.llm_client import MockClient, get_client
from ai.orchestrator import handle
from ai.retriever import KnowledgeBase
from ai.trace import setup_logging
from ai_demo import build_household, next_monday

LIVE_DELAY_SECONDS = 4  # stay under the free tier's requests-per-minute limit


def fill_dates(value, today: date):
    """Replace {today}/{tomorrow}/{next_monday} placeholders in a mock reply."""
    text = json.dumps(value) if not isinstance(value, str) else value
    for key, day in {"{today}": today, "{tomorrow}": today + timedelta(days=1),
                     "{next_monday}": next_monday(today)}.items():
        text = text.replace(key, day.isoformat())
    return text


def check(case: dict, reply, today: date) -> list[str]:
    """Return the list of ways the reply differs from what the case expects."""
    expect, problems = case["expect"], []
    kinds = expect["kind"] if isinstance(expect["kind"], list) else [expect["kind"]]
    if reply.kind not in kinds:
        return [f"expected {'/'.join(kinds)}, got {reply.kind}"]

    if "mentions" in expect and expect["mentions"] not in reply.message.lower():
        problems.append(f"answer should mention '{expect['mentions']}'")
    if "source" in expect and not any(expect["source"] in s for s in reply.sources):
        problems.append(f"should cite {expect['source']}, cited {reply.sources}")

    if reply.proposal:
        task = reply.proposal.task
        want_due = {"today": today, "tomorrow": today + timedelta(days=1),
                    "next_monday": next_monday(today)}
        checks = {
            "pet": reply.proposal.pet_name,
            "frequency": task.frequency,
            "category": task.category,
            "priority": task.priority,
            "duration": task.duration_minutes,
            "start_time": task.start_time.strftime("%H:%M") if task.start_time else None,
        }
        for field, got in checks.items():
            if field in expect and expect[field] != got:
                problems.append(f"{field}: expected {expect[field]}, got {got}")
        if "due" in expect and task.due_date != want_due[expect["due"]]:
            problems.append(f"due: expected {want_due[expect['due']]}, got {task.due_date}")
        if expect.get("conflict") and not reply.proposal.warnings:
            problems.append("should warn about a conflict")
    return problems


def describe(case: dict) -> str:
    """Short 'expected' text for the results table."""
    expect = dict(case["expect"])
    kind = expect.pop("kind")
    kind = "/".join(kind) if isinstance(kind, list) else kind
    details = ", ".join(f"{k}={v}" for k, v in expect.items())
    return f"{kind}" + (f" ({details})" if details else "")


def main() -> int:
    """Run all cases, print and save the results table, and return an exit code."""
    parser = argparse.ArgumentParser(description="Evaluate the PawPal+ AI Assistant")
    parser.add_argument("--mock", action="store_true", help="use scripted replies (offline)")
    args = parser.parse_args()

    setup_logging()
    today = date.today()
    cases = json.load(open("eval_cases.json", encoding="utf-8"))
    kb = KnowledgeBase("knowledge")
    if args.mock:
        client = MockClient({c["input"]: fill_dates(c["mock"], today)
                             for c in cases if c["mock"] is not None})
    else:
        client = get_client()
        if client is None:
            print("No GEMINI_API_KEY found. Add it to .env, or run: python evaluate.py --mock")
            return 2

    rows, passed, by_group = [], 0, {}
    for case in cases:
        calls_before = len(getattr(client, "prompts", []))
        started = clock.perf_counter()
        reply = handle(case["input"], build_household(today), kb, client, today)
        problems = check(case, reply, today)
        ok = not problems
        passed += ok
        group = by_group.setdefault(case["group"], [0, 0])
        group[0] += ok
        group[1] += 1
        got = reply.kind + (f": {reply.proposal.summary()}" if reply.proposal else "")
        rows.append(f"| {case['id']} | {case['input']} | {describe(case)} | {got} | "
                    f"{'PASS' if ok else 'FAIL: ' + '; '.join(problems)} |")
        print(f"{case['id']:3} {'PASS' if ok else 'FAIL'}  {case['input']}"
              + ("" if ok else f"\n      {'; '.join(problems)}"))
        used_ai = any(step in ("PARSE", "GENERATE") for step, _ in reply.trace.steps)
        if not args.mock and used_ai:
            clock.sleep(LIVE_DELAY_SECONDS)

    mode = "mock (scripted replies)" if args.mock else client.name
    summary = [f"**{passed}/{len(cases)} cases passed** - mode: {mode} - {today.isoformat()}", ""]
    summary += [f"- {group}: {ok}/{total}" for group, (ok, total) in by_group.items()]
    table = ["| ID | Input | Expected | Got | Result |", "|---|---|---|---|---|"] + rows
    report = "\n".join(["# PawPal+ AI Evaluation Results", ""] + summary + ["", *table, ""])
    suffix = "_mock" if args.mock else ""
    with open(f"eval_results{suffix}.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("\n" + "\n".join(summary))
    print(f"Saved eval_results{suffix}.md")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
