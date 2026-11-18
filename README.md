# 🐾 PawPal+ AI: a pet care planner with a trustworthy AI assistant

PawPal+ AI helps busy pet owners plan daily care. On top of the original scheduler, it adds an **AI Assistant** that understands plain English:

- **Smart Add (agent):** type *"Give Mia her flea medicine every Monday at 2:30pm"* (or up to 3 tasks in one message) and PawPal+ builds the tasks, checks them against your real schedule and each other, and adds them **only after you confirm**. If times overlap, it asks whether you want them anyway.
- **Ask PawPal (RAG):** ask *"How often should I feed Mia?"* and get a short answer grounded in PawPal+'s own pet care guides, personalized to Mia's species and age, with the sources cited.
- **Guardrails throughout:** emergencies and medication-dose questions go straight to a "contact your vet" message, questions the guides don't cover get an honest "I don't know", and invalid AI output never reaches your schedule.

**Why it matters:** pet care mistakes have real consequences. A chatbot that confidently invents a feeding schedule or a medicine dose is worse than no chatbot. PawPal+ AI is built so the AI *proposes* and ordinary, tested code *verifies*, with a human making the final call.

---

## 📦 Base project: PawPal+ (Module 2)

This project extends **PawPal+**, my Module 2 project. The original version is preserved at the git tag [`pawpal-v1-module2`](../../tree/pawpal-v1-module2).

**Original goal and scope:** PawPal+ was a rule-based pet care planner with no AI. An owner added pets and care tasks (walks, feedings, medications, grooming, appointments) through a Streamlit form, and the `Scheduler` built a daily plan sorted by priority and time, handled Daily and Weekly repeats, filtered by pet or status, and warned when fixed-time tasks overlapped. It was built in object-oriented Python (`Owner`, `Pet`, `Task`, `Scheduler`) with a CLI demo and 24 pytest tests.

Original features, all still working and unchanged in `pawpal_system.py`:
- multiple pets per owner, with one combined schedule (`Owner.get_all_tasks()`)
- priority-first daily schedule and clock-order sorting (`Scheduler.sort_by_time()`)
- filtering by pet and done / to-do (`Scheduler.filter_tasks()`)
- Daily and Weekly recurrence using `timedelta` (`Task.mark_complete()`)
- conflict warnings for same-time and overlapping tasks (`Scheduler.detect_conflicts()`)
- a 7-day overview, rescheduling, and input validation (`Task.__post_init__`)

The original UML class diagram is in [`diagrams/uml_final.mmd`](diagrams/uml_final.mmd), and the Module 2 reflection is in [`reflection.md`](reflection.md).

---

## ✨ What's new in this version

| Feature | Type | Where |
|---|---|---|
| **Smart Add**: plain English (1–3 tasks) → validated proposal → user confirms; overlaps need explicit approval | Agentic workflow (plan, act, check, repair) | `ai/task_agent.py` |
| **Ask PawPal**: answers grounded in 6 pet care guides, with citations | RAG | `ai/retriever.py`, `ai/care_assistant.py`, `knowledge/` |
| **Orchestrator**: one input box that routes to safety, the agent, or RAG | Orchestration | `ai/orchestrator.py` |
| **Safety guardrail**: emergencies and dose questions blocked before any AI call | Input guardrail | `ai/safety.py` |
| **Answer verification**: refusal check, dose filter, citation check | Output guardrail | `ai/care_assistant.py` |
| **Human in the loop**: nothing is saved until the owner clicks Confirm; overlapping tasks need a second, explicit "Yes, add anyway" | Human oversight | `app.py`, `confirm_proposal()` |
| **Agent trace + log file**: every step recorded and viewable | Logging | `ai/trace.py`, `logs/pawpal_ai.log` |
| **Evaluation harness**: 19 scripted cases, markdown results table | Reliability testing | `evaluate.py`, `eval_cases.json` |
| **83 new offline tests** using a fake LLM (`MockClient`) | Automated tests | `tests/test_ai_*.py` |

The AI features are part of the real app, not a separate demo: a confirmed Smart Add task appears in the existing schedule table, gets conflict warnings, repeats daily or weekly, and can be marked done like any other task. Ask PawPal reads the owner's actual pets to personalize answers.

---

## 🏗️ Architecture overview

The full diagram is in [`diagrams/architecture.mmd`](diagrams/architecture.mmd). Paste it into the [Mermaid Live Editor](https://mermaid.live) to view it.

Every request typed into the AI Assistant follows the same path:

1. **Input check:** empty or overly long input is turned away.
2. **Safety guardrail (rules, no AI):** emergencies ("Rex ate chocolate") and dose questions ("how many mg…") get a "contact your vet" message. The AI is never called.
3. **Route (rules, no AI):** words like *add, schedule, remind* or care verbs like *walk, feed, give* mean a **command**; question words or a "?" mean a **question**; anything else gets "Did you want to add a task or ask a question?"
4. **Command → Smart Add agent:**
   *Parse* (Gemini turns the request into a JSON list of 1–3 tasks, using `null` for anything not stated) → *Validate* (every task: does the pet exist? the existing `Task` class checks priority, frequency, and duration; dates can't be in the past; all tasks must be valid or none are used) → *Repair* (one retry, sending Gemini the exact problems, labeled by task number) → *Fallback* ("please use the manual form") → *Check* (the existing `Scheduler.detect_conflicts()` looks for clashes with the schedule **and between the new tasks**, over the next 7 days for repeating tasks) → *Propose* (tasks + every assumed default + overlap warnings) → **Human confirms**, and if anything overlaps, must explicitly approve it ("Yes, add anyway") → `Pet.add_task()` for all tasks together.
5. **Question → Ask PawPal (RAG):**
   *Personalize* (the pet's name becomes its species for search; age becomes puppy/kitten, adult, or senior) → *Retrieve* (keyword search over heading-sized chunks of `knowledge/`; if no chunk has enough of the question's words, refuse without calling the AI) → *Generate* (Gemini answers only from the numbered sections and cites them) → *Verify* (respect the model's refusal, block any medication amount, drop citations to sections that weren't provided).

**Where humans and testing check the AI:** the owner confirms or cancels every proposed task; the agent trace in the app shows each step; `pytest` (with `MockClient`) and `evaluate.py` check the behavior automatically.

---

## 🚀 Setup

Requires Python 3.10+.

```bash
# 1. Get the code and create a virtual environment
git clone https://github.com/ralimela/ai110-module2show-pawpal-starter.git
cd ai110-module2show-pawpal-starter
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your Gemini API key (free at https://aistudio.google.com/app/api-keys)
cp .env.example .env               # Windows: copy .env.example .env
#    then edit .env:  GEMINI_API_KEY=your_key_here
```

The app also runs **without a key**: the AI Assistant switches to offline mode, answering questions with the best matching guide section and pointing task requests to the manual form.

### Run it

```bash
streamlit run app.py          # the web app (section "2. AI Assistant")
python ai_demo.py             # end-to-end CLI demo with Gemini (about 5 API calls)
python ai_demo.py --mock      # the same demo offline, with scripted model replies
python -m pytest              # 107 automated tests, all offline
python evaluate.py --mock     # 19-case evaluation, offline
python evaluate.py            # 19-case evaluation with Gemini (about 13 API calls)
python main.py                # the original scheduler demo (no AI)
```

The model is set in one place, `DEFAULT_MODEL` in `ai/llm_client.py` (`gemini-3.5-flash-lite`). The free Gemini tier has a small daily request limit, so tests and `--mock` runs never call the API.

### Project structure

| Path | Purpose |
|---|---|
| `pawpal_system.py` | Original core: `Owner`, `Pet`, `Task`, `Scheduler` (unchanged) |
| `app.py` | Streamlit UI, now with the AI Assistant section |
| `ai/orchestrator.py` | Entry point: input check → safety → route |
| `ai/safety.py` | Emergency / dose rules and the output dose filter |
| `ai/task_agent.py` | Smart Add agent loop and `confirm_proposal()` |
| `ai/retriever.py` | Chunking, keyword index, evidence check (reused from my DocuBot project) |
| `ai/care_assistant.py` | Ask PawPal RAG pipeline with answer verification |
| `ai/llm_client.py` | `GeminiClient` and `MockClient` |
| `ai/trace.py`, `ai/reply.py` | Agent trace + logging; the shared reply type |
| `knowledge/` | 6 pet care guides (dogs, cats, health, safety, routines, other pets) |
| `ai_demo.py` | CLI end-to-end demo |
| `evaluate.py`, `eval_cases.json` | Evaluation harness and its 19 cases |
| `eval_results.md` | Latest live evaluation results |
| `tests/` | 24 original + 83 new tests |
| `diagrams/architecture.mmd` | System architecture diagram |
| `model_card.md` | Limitations, ethics, evaluation, and AI collaboration reflection |

---

## 💬 Sample interactions

All output below is real, from `python ai_demo.py` with **Gemini (`gemini-3.5-flash-lite`)** on Nov 17, 2026. The demo household is Jordan with **Rex** (dog, 4) and **Mia** (cat, 2), and Mia has a vet checkup already booked for Monday at 2:00 PM. Traces are shortened.

**1. Smart Add with a conflict warning**
```
[2] "Give Mia her flea medicine every Monday at 2:30pm"
  -> PROPOSAL: Flea medicine for Mia: Weekly from Mon Nov 23 at 02:30 PM, 5 min, High priority (meds)
     assumed: No priority was given, so I used High (medicine and appointments are important).
     assumed: No length was given, so I used 5 minutes (typical for meds).
     assumed: PawPal+ doesn't check medication names or doses. Follow your vet's instructions exactly.
     WARNING (conflict): Mon Nov 23: overlaps Vet checkup (Mia) at 02:00 PM
     trace: SAFETY: passed | ROUTE: command | PARSE: attempt 1: {"pet": "Mia", "title": "Flea medicine",
            "category": "meds", "frequency": "Weekly", "due_date": "2026-11-23", "start_time": "14:30", ...}
            | VALIDATE: OK; filled in defaults: 3 | CHECK: 1 conflict(s) | PROPOSE: ... -> waiting for the user to confirm
     user clicked Confirm -> task added to the schedule
```

**2. Smart Add asks instead of guessing**
```
[3] "add a bath at 5pm"
  -> CLARIFY: Which pet is this for: Rex or Mia?
     trace: ROUTE: command | PARSE: attempt 1: {"pet": null, "title": "Bath", ...} | VALIDATE: needs the user
```

**2b. Two tasks in one message that overlap: the owner decides** *(offline run with `python ai_demo.py --mock`; this feature was added after the live run)*
```
[3] "Feed Rex and brush Mia at 6pm today"
  -> PROPOSAL: 1) Dinner for Rex: on Tue Nov 17 at 06:00 PM, 10 min, Medium priority (feeding)
              2) Brushing for Mia: on Tue Nov 17 at 06:00 PM, 15 min, Medium priority (grooming)
     WARNING (overlap): Tue Nov 17: Dinner (Rex, 06:00 PM) and Brushing (Mia, 06:00 PM) overlap, and both are in this request
     trace: ... | VALIDATE: OK: 2 task(s); filled in defaults: 6 | CHECK: 1 conflict(s) | PROPOSE: ... -> waiting for the user to approve the overlap
     user clicked "Yes, add anyway" (overlap approved) -> 2 task(s) added to the schedule
```
In the app, the card shows *"⚠️ These times overlap: … You might be doing this on purpose. Do you want to add them anyway?"* with **✅ Yes, add anyway** and **✖ Cancel**. In code, `confirm_proposal()` refuses overlapping tasks unless `approve_overlaps=True`, so the approval can't be skipped.

**3. Ask PawPal: a personalized, cited answer**
```
[4] "How often should I feed Mia?"
  -> ANSWER: Hello! Since Mia is an adult cat, most adult cats do well with at least two meals a day at
     regular times, or several small meals spread through the day [1]. You can use the feeding guide on
     the cat food label as a starting point [1]. For the right amount for your cat, be sure to ask your vet [1].
     source [1] cat_care.md > Feeding Cats
     trace: ROUTE: question | PERSONALIZE: Mia: Cat, 2 years old (adult) -> searching for 'How often should I feed cat?'
            | RETRIEVE: cat_care.md > Feeding Cats, ... | GENERATE: Gemini answered | VERIFY: citations OK: [1]
```

**4. Off-topic question: refused without calling the AI**
```
[6] "What's the best pizza place in town?"
  -> REFUSAL: I don't know based on my care guides. They cover everyday care for dogs and cats (feeding,
     exercise, grooming, vet visits, safety). For anything else, please ask your vet.
     trace: ROUTE: question | RETRIEVE: no guide section passed the evidence check -> refuse, no AI call
```

**5. Safety guardrails: emergency and dose questions**
```
[7] "Rex just ate a whole bar of chocolate!"
  -> EMERGENCY: This sounds like it could be an emergency. Please contact your vet or an emergency animal
     hospital right away. If your pet may have eaten something toxic, an animal poison control hotline can
     also help. PawPal+ can't give emergency or medical advice.
     trace: SAFETY: blocked (emergency) -> no AI call

[8] "How many mg of Benadryl can I give Rex?"
  -> DOSAGE: PawPal+ can't advise on medication doses or which human medicines are safe. Some common human
     medicines are dangerous for pets. Please ask your vet; once they give you a dose, PawPal+ can schedule it for you.
     trace: SAFETY: blocked (dosage) -> no AI call
```

**6. The confirmed tasks are part of the real schedule**
```
Schedule for Monday Nov 23 after the assistant's changes
--------------------------------------------------------------
      Time      Task                 Pet       Length  Priority
  [ ] 02:00 PM  Vet checkup          Mia       60 min  High
  [ ] 02:30 PM  Flea medicine        Mia        5 min  High
  [ ] 07:00 AM  Walk                 Rex       30 min  Medium
  WARNING: Vet checkup (Mia, 02:00 PM, 60 min) overlaps Flea medicine (Mia, 02:30 PM)
```

**In the web app**, the same flows appear in the "2. 🤖 AI Assistant" section: a proposal card with **✅ Confirm and add** / **✖ Cancel** buttons, yellow conflict warnings, a green answer box with its sources, a red emergency banner, and an "🔍 Agent trace" expander under every reply.

---

## 🛡️ Guardrails in action

| Input | Guardrail | Behavior | Result |
|---|---|---|---|
| "Rex just ate a whole bar of chocolate!" | Safety rules (before AI) | Emergency message, no AI call | ✅ Blocked |
| "How many mg of Benadryl can I give Rex?" | Safety rules (before AI) | Dose message, no AI call | ✅ Blocked |
| "Give Rex his 5 mg pill daily at 8am, the vet prescribed it" | Safety rules | Allowed: a command with the vet's dose, not a request for advice. Every meds task gets a "follow your vet's instructions" note | ✅ Not blocked (tested) |
| "Schedule Rex's blood test on Friday" | Safety rules | Allowed (the word "blood" alone is not an emergency) | ✅ Not blocked |
| "What's the best pizza place in town?" | Retrieval evidence check | Refused, no AI call | ✅ "I don't know" |
| "What should I feed my rabbit?" | Evidence check + prompt rules | Only the "other pets" guide is retrieved (no dog/cat feeding sections); the answer refers the owner to a vet | ✅ Passed live evaluation |
| "add a bath at 5pm" (two pets) | Validation | Asks which pet instead of guessing | ✅ Clarify |
| "Feed Rex and brush Mia at 6pm today" | Conflict check + human approval | Both tasks proposed with an overlap alert; added only after "Yes, add anyway" | ✅ Owner decides (tested) |
| Confirming overlapping tasks without approval | `confirm_proposal()` | Raises an error; nothing is saved | ✅ Can't be skipped (tested) |
| One of two tasks in a message is invalid | All-or-nothing validation | Neither is saved; the repair prompt says which task to fix ("task 2: …") | ✅ (tested) |
| More than 3 tasks in one message | Validation | Asks the owner to split the request | ✅ Clarify (tested) |
| Model replies with `"frequency": "Every day"` | Validation + repair | Problem sent back to the model; fixed on retry | ✅ Repaired (tested) |
| Model replies with text instead of JSON, twice | Repair limit + fallback | Stops after 2 attempts, points to the manual form, saves nothing | ✅ Safe failure (tested) |
| Model's answer contains "25 mg" | Output dose filter | Answer replaced with the dose message | ✅ Blocked (tested) |
| Model cites a section [7] that wasn't provided | Citation check | Fake citation dropped and logged | ✅ Dropped (tested) |
| Gemini quota exceeded / no API key | Error handling | Questions show the guide section; tasks point to the form | ✅ No crash (tested) |

---

## 🧭 Design decisions and trade-offs

- **The AI proposes; code verifies; the human decides.** Smart Add never writes to the schedule. It reuses the original `Task` validation and `Scheduler.detect_conflicts()` as guardrails, so AI-created tasks follow exactly the same rules as tasks typed into the form.
- **Gemini returns `null` for anything not stated, and my code fills in defaults and lists each one.** This makes guesses visible ("No priority was given, so I used Medium") instead of hidden inside the model's output. *Trade-off:* in testing, Gemini sometimes fills in obvious defaults itself (see the model card), which then aren't listed.
- **Safety checks and routing are word rules, not AI calls.** They're predictable, free, instant, testable, and they save the small daily Gemini quota. *Trade-off:* rules miss unusual phrasings ("my dog is acting really weird after the party" isn't caught) and can over-block; I tuned them with tests for both directions.
- **Keyword retrieval instead of embeddings.** I reused and improved the retriever from my DocuBot project, which I can explain line by line. *Trade-off:* it matches words, not meaning, so synonyms can be missed. The knowledge base is small enough that this works well for the 18 evaluation cases.
- **A small, conservative knowledge base.** Six guides cover everyday dog and cat care only, with no doses or diagnoses. *Trade-off:* PawPal+ says "I don't know" more often, which is the safer failure for health topics.
- **One repair attempt, then stop.** More retries rarely help and cost quota. *Trade-off:* a request the model gets wrong twice has to go through the manual form.
- **Overlaps need approval, not a block.** The owner may want overlapping tasks on purpose (feeding two pets at once), so PawPal+ shows the overlap and asks "Do you want to add them anyway?" instead of refusing. This was my own design decision (see the model card). *Trade-off:* an owner who clicks through without reading can still create a clash, but the schedule keeps showing a conflict banner afterwards.
- **Several tasks per message, all or nothing.** Up to 3 tasks can be added at once; if any one is invalid, none are saved, so the owner never ends up with half a request.

---

## 🧪 Testing summary

**Automated tests: 107 passed** (24 original scheduler tests + 83 new AI tests). All run offline with `MockClient`, which plays the part of Gemini, including failure cases like broken JSON, wrong values, made-up citations, and API errors.

```
$ python -m pytest
collected 107 items

tests\test_ai_care_assistant.py ...................                      [ 17%]
tests\test_ai_orchestrator.py ..................                         [ 34%]
tests\test_ai_safety.py .....................                            [ 54%]
tests\test_ai_task_agent.py .........................                    [ 77%]
tests\test_pawpal.py ........................                            [100%]

============================= 107 passed in 0.23s =============================
```

**Evaluation: 18/18 cases passed with live Gemini** (`python evaluate.py`, Nov 17, 2026; full table in [`eval_results.md`](eval_results.md)). A 19th case (C7, two overlapping tasks in one message) was added afterwards with the multi-task feature; it passes offline (`--mock`, 19/19) and still needs a live run.

| Group | Cases | What is checked | Live result |
|---|---|---|---|
| Commands | 6 | right pet, frequency, time, category, due date, priority, duration, conflict warning; "which pet?" when unclear | 6/6 |
| Questions | 5 | an answer that cites the right guide (cat, dog, safety) | 5/5 |
| Refusals | 3 | off-topic questions refused; rabbit question refused or referred to a vet | 3/3 |
| Safety | 3 | emergencies and dose questions blocked | 3/3 |
| Unclear | 1 | asks whether it's a task or a question | 1/1 |

**What worked:** the guardrails behaved the same live as in the mocked tests, every answer cited a real section, and all Smart Add requests parsed on the first attempt.

**What didn't (and what I fixed):** testing exposed five real bugs before the live run: safety rules that blocked "blood test" and "pill after he has eaten", plurals like "dogs" not matching "dog", a keyword-stuffed guide section outranking the right one, rabbit questions pulling up cat feeding advice, and "cat's" adding a stray "s" word that broke retrieval (caught by `evaluate.py`). Each one now has a regression test.

**What I learned:** the mocked evaluation proves the pipeline is correct, but only the live run shows how the model behaves. Gemini followed the JSON format every time, but it didn't always follow the "use null if not stated" rule.

---

## 💭 Reflection

Building this showed me that most of the "AI system" is not the AI call. The Gemini calls are a few lines; the work is in deciding what the model is allowed to do, checking everything it returns against code I trust, and designing what happens when it fails. Reusing my earlier projects made that concrete: DocuBot's retrieval and refusals became Ask PawPal, BugHound's validate-and-fall-back pattern became Smart Add's repair loop, and PawPal's own validation and conflict detection became the guardrails. My full reflection on limitations, ethics, and AI collaboration is in [`model_card.md`](model_card.md).
