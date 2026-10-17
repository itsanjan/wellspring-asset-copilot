# Wellspring: Asset Intelligence Copilot

_Concept and proposal · FlytBase Hack Day, Hacktoberfest 2026_

> Status: **proposal**. Stack and model choices are recommendations awaiting sign-off.

## 1. The problem, in short

Operators of a 200-well oil field must dig through inspections, repair records, regulator
events, long manuals and people's notes to answer simple questions ("which wells were down
longest this summer, and why?"). That takes hours, so a stopped or leaking well can go
unnoticed for weeks.

We need a chat copilot that answers in seconds, **correctly, with cited evidence, and with
the right visual**, while remembering what users tell it and being honest when sources
disagree.

What makes it hard:

| Trap | Consequence for design |
|---|---|
| "Producing oil" and "downtime" are never stored; they derive from a per-type temperature rule and a 7-days-per-inspection rule | Derivations must be deterministic code, not LLM arithmetic |
| `recorded_status` is stale; notes and corrections override raw detections | A layered "effective truth" model, never mutating originals |
| Some facts live only in free-text repair reports (e.g. containment time) | Documents must be turned into structured facts, with citations |
| Rules/deadlines live in manuals and SOPs | Rules are retrieved and applied, not hardcoded to the sample |
| Corrections have scope (one well vs. a whole well type); memory is shared and private | Scoped, attributed, append-only feedback store |
| A test dataset with unseen wells and questions arrives late | General tools and a data-driven loader; no per-question logic |

## 2. The solution: Wellspring

One idea: **the LLM decides what to ask; deterministic tools decide what is true.**

Wellspring is an agent that plans, calls a small set of typed tools over a layered store,
and returns an answer plus evidence plus a visual specification that the UI renders inline.

Principles:

1. **Compute, don't recall.** Numbers (downtime, spill totals, costs) come from tool code.
   The model never does the arithmetic.
2. **Effective truth = raw data + corrections + shared notes.** Every tool reads through
   this overlay, so a correction made by one user shows up for all (at its scope).
3. **Evidence on every claim.** Each tool returns record IDs and document sections; the
   answer cites them.
4. **Conflicts are first-class.** When data, notes and user claims disagree, the copilot
   shows both sides and marks unconfirmed claims. It never picks silently.
5. **Generate the visual from the answer's shape** (card, table, line, bar, map, heatmap),
   unless the user names one.
6. **General by construction.** Rules are read from the manuals/SOPs, schemas are loaded
   from the files, and nothing references WELL-0001 or a sample question.

## 3. Architecture

```
                    ┌──────────────────────────── Chat UI (user picker) ───────────────────────────┐
                    │  messages · inline generative visuals · citations · "who corrected what"     │
                    └───────────────▲──────────────────────────────────────────┬───────────────────┘
                                    │ answer + evidence + visual spec          │ question + user_id
                              ┌─────┴──────────────────────────────────────────▼──────┐
                              │                    Agent orchestrator                  │
                              │  plan → call tools → verify → compose → pick visual   │
                              │  prompt = tool schemas + user memory + shared notes   │
                              └───┬───────────┬───────────┬───────────┬───────────────┘
                                  │           │           │           │
                        ┌─────────▼──┐ ┌──────▼─────┐ ┌───▼──────┐ ┌──▼────────────┐
                        │ Data tools │ │ Rules/docs │ │ Report   │ │ Memory tools  │
                        │ (SQL/code) │ │ retrieval  │ │ facts    │ │ (read/write)  │
                        └─────────┬──┘ └──────┬─────┘ └───┬──────┘ └──┬────────────┘
                                  │           │           │           │
 ┌────────────────────────────────▼───────────▼───────────▼───────────▼──────────────┐
 │  Layered store                                                                    │
 │  L0 raw (immutable): wells, inspections, repairs, regulator events, notes          │
 │  L1 derived views: producing flag, downtime, spill status, repair counts           │
 │  L2 documents: manuals/SOPs chunked by section, repair reports + extracted facts   │
 │  L3 overlay: corrections (scope: well | type), shared well notes, claims+status    │
 │  L4 private: per-user conversations and entity references                          │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

### Storage and retrieval

- **Structured data → SQLite/DuckDB**, loaded by a generic loader that walks `assets/*`.
  A reload against the test dataset is one command.
- **Derived views** (L1) implement the rules once: producing = `wellhead − ambient ≥ type
  threshold`, downtime = 7 days per non-producing inspection, real spill = detected and
  not corrected. Thresholds are **parsed from the manuals**, with a visible fallback if
  parsing fails, so a new well type does not silently break.
- **Documents**: manuals/SOPs chunked by heading with section IDs for citation; repair
  reports parsed (offline, once at load) into structured facts such as spill containment
  time, classification and "action required", keeping a pointer to the source file via
  `report_file`. Keyword + vector search over chunks for rule lookup.
- **Overlay (L3)** is append-only: each entry has target (record or well type), scope,
  new value, original value, author, reason, timestamp, and status (`confirmed`,
  `unconfirmed`, `superseded`). Effective values are computed at read time.

### Tools (proposed set)

| Tool | Purpose |
|---|---|
| `well_status(well)` | Effective status, latest inspection, reason, stale-record flag |
| `query_inspections(filters, window)` | Raw/effective readings for charts |
| `downtime(scope, window)` | Ranked downtime by well/area |
| `spills(scope, window)` | Real spills after corrections, with reportability class |
| `repairs(scope, window, filters)` | Repairs, counts, costs |
| `compliance_check(well, window)` | SR/RC breaches and open actions, using SOP deadlines plus report facts |
| `search_docs(query)` | Manual/SOP/report passages with citations |
| `resolve_entities(text)` | Map "the three wells we discussed yesterday" to IDs from the user's memory |
| `record_feedback(...)` | Store correction/rule/claim with scope, author, reason |
| `get_context(well)` | Shared notes and unconfirmed claims for a well |
| `render(visual, data)` | Emit a visual spec (card/table/line/bar/map/heatmap) |

### Memory

- **Shared well knowledge**: notes and claims attach to a well (or a well type) and are
  injected whenever that well is touched, attributed to their author.
- **Private memory**: conversations and entity references are keyed by `user_id` and are
  only readable with that user's ID, enforced in code, not by the prompt.
- **Feedback learning**: a correction to raw data or to AI analysis is stored as an
  overlay entry at its stated scope (one well, or all wells of a type) and used by every
  later answer for every user, with the original kept visible.

### Conflict handling

A conflict check runs before composing any answer that touches a user claim or note:
claim vs. latest inspection, claim vs. another claim, note vs. note, correction vs. the
inspector's own note. It outputs both sources, which one the data supports, and a request
for confirmation. Unconfirmed claims never override sensor-derived status.

### Visual selection

A fixed mapping from answer shape to component (one well → card, list → table, series →
line, comparison → bar, locations → map, two dimensions → heatmap), overridden when the
user names a visual.

## 4. Proposed stack (to confirm)

| Layer | Proposal | Why |
|---|---|---|
| Backend | Python, FastAPI | Fast to build; strong data tooling |
| Store | SQLite or DuckDB, plus a local vector index | No infra; reloads fast |
| LLM | Hosted model with tool calling for the copilot; Gemma 4 via AI Studio/OpenRouter as the free fallback | Cost is part of the write-up |
| UI | React chat with chart/map/table components | Generative UI inline |
| Tests | Oracle scripts and a replay harness for Q1–Q18 | Proves correctness without the LLM |

## 5. Build steps

1. **Data layer**: generic loader, derived views, oracle tests for Q1–Q8 _(oracle for Q1–Q8 done)_.
2. **Document layer**: chunk manuals/SOPs; parse repair reports into facts; oracle for Q9–Q10.
3. **Tools + agent loop**: typed tools, citations, answer composition, first end-to-end Q1.
4. **Generative UI**: card, table, line, bar, map, heatmap; wire to the agent output.
5. **Overlay + memory**: corrections, scoped rules, private memory, user picker (Q11–Q14).
6. **Multi-user + conflicts**: shared notes, unconfirmed claims, disagreement display (Q15–Q18).
7. **Generalization hardening**: re-run against a perturbed copy of the data and unseen question styles.
8. **Submission**: write-up (design, results), 5-minute video, public MIT repo.

## 6. Todos

**Done**
- [x] Read problem statement, dataset guide, example questions, manuals and SOPs
- [x] Profile record counts against the guide
- [x] Oracle reproducing Q1–Q8 (`oracle/oracle.py`)

**Next**
- [ ] Sign off the stack and model choice (see Open questions)
- [ ] Profile data quirks: report filename variants, nulls, missed inspections, drilled-later wells
- [ ] Parse repair reports into structured facts; oracle for Q9 and Q10
- [ ] Write the loader and derived views as the product code, not just the oracle
- [ ] Draft tool schemas and agent prompt
- [ ] Build the UI shell with user picker

**Later**
- [ ] Overlay store and scoped feedback (Q12–Q14)
- [ ] Conflict detector (Q15–Q18)
- [ ] Perturbed-data test for generalization
- [ ] Cost tracking per answer for the write-up
- [ ] Write-up, video, public repo with license

## 7. Open questions

1. Which model(s) will run the copilot, and what is the budget (free tier vs. paid)?
2. Python/FastAPI plus React acceptable, or another stack?
3. Build in this repo or a new public repo of your own? The submission needs a public
   open-source repo that is yours.
4. Anything in the time-sensitive final two hours (loading the test dataset) we should
   automate now, such as a single `make load DATA=...` command?
