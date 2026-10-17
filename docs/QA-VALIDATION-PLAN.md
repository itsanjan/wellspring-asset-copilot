# QA Validation Plan

Scope: Wellspring, the asset-intelligence copilot for the FlytBase Hack Day challenge.
This is the single place that says **what must be true, how we prove it, and whether it is proven yet**.

- Last updated: 2026-10-17
- Suite at last run: **56 tests: 53 pass, 2 skipped, 1 expected failure.** The 2 skipped are the live-app e2e tests (waiting for the app). The expected failure is a known bug, see section 7.
- Source of truth for expected answers: [`problem-statement/03-evaluation-queries.md`](../problem-statement/03-evaluation-queries.md)

## How to read status

| Mark | Meaning |
|---|---|
| ✅ | Automated, runs in CI, passing |
| 🟡 | Partly covered, or covered only by a script/manual check, gap noted |
| ⬜ | Not started (the thing exists or can exist, but there is no test yet) |
| ⛔ | Blocked: the component under test is not built yet |
| 👁 | Manual check by design (judgement, demo, writing) |

Update the status column in the same commit that changes the code or the test. A row only turns ✅ when a named test proves it.

## 1. Test layers

| Layer | What it proves | Where | Run | Status |
|---|---|---|---|---|
| L1 Unit | One module in isolation (rule parsing) | `tests/test_rules.py` (14) | `python3 -m unittest discover -s tests -t .` | ✅ |
| L2 Smoke | Ground-truth script still runs and prints Q1-Q8 | `tests/test_ground_truth.py` (4) | same | ✅ |
| L3 Integration / parity | Product SQL views == LLM-free ground truth on all 200 wells, and == organizers' expected answers | `tests/test_parity.py` (17) | same | ✅ |
| L4 E2E harness | The answer scorer is not vacuous; all 18 questions present in order | `tests/e2e/test_harness.py` (7) | same | ✅ |
| L5 E2E live app | Question in, answer + visual + evidence out, scored against expected | `tests/e2e/test_copilot.py` (2, covers Q1-Q18) | `COPILOT_URL=... python3 -m unittest tests.e2e.test_copilot -v` | ⛔ app not built |
| L6 Generalization | Same code on perturbed, synthetic and second datasets | `tests/test_generalization.py` (12), builders in `tests/synth.py` | same | ✅ on synthetic data; ⬜ on the organizers' test data until it arrives |
| L7 Non-functional | Latency, cost, security, resilience | see section 5 | n/a | mostly ⬜ |
| CI | Layers 1-4 on every push and PR, Python 3.9 and 3.12 | `.github/workflows/ci.yml` | GitHub Actions | 🟡 written, not yet run on GitHub |

The e2e contract (HTTP or Python adapter) is documented in `tests/e2e/__init__.py`. Verified: an empty stub copilot fails the live-app tests with 131 "missing fact" messages, so a wrong app cannot pass.

## 2. Functional scenarios

### 2.1 Data layer (load, rules, derived truth)

| ID | Scenario | Pass criteria | Proof | Status |
|---|---|---|---|---|
| F-D01 | Load the full dataset | 200 wells; inspection count equals the raw files | `LoadTest.test_counts_match_dataset_guide` | ✅ |
| F-D02 | Load is idempotent | Two loads give identical counts | `LoadTest.test_load_is_idempotent` | ✅ |
| F-D03 | Every well type has a producing threshold, parsed from its manual | No `producing IS NULL` rows; load warns otherwise | `LoadTest.test_every_well_type_has_a_threshold` | ✅ |
| F-D04 | Thresholds come from manuals with a citable section | T200 15 °C (s9.3), T350 20 °C (s3), RCA 3 in 12 months | `SampleDataTest` in `test_rules.py` | ✅ |
| F-D05 | New well type needs no code change | A synthetic T500 manual is picked up | `ParseAllTest.test_new_well_type_needs_no_code_change` | ✅ |
| F-D06 | Missing rule is inherited and labelled, never silently guessed | T350 inherits RCA from T200, marked "inherited by T350" | `ParseAllTest` | ✅ |
| F-D07 | Producing status matches ground truth on every inspection (39,567) | Set of non-producing inspection IDs identical | `ParityWithGroundTruthTest.test_producing_flag_for_every_inspection` | ✅ |
| F-D08 | Real spills exclude corrected detections | Identical to ground truth on all wells; INS-0001-0070 excluded | `test_real_spills_for_every_well`, Q4 test | ✅ |
| F-D09 | Downtime = 7 days per non-producing inspection, missed inspections add nothing | Per-well parity for 2025 and the last 4 months | `test_downtime_per_well_in_2025_and_last_4_months` | ✅ |
| F-D10 | Latest inspection per well | Parity for all 200 wells | `test_latest_inspection_per_well` | ✅ |
| F-D11 | Repair-report facts parsed from the compliance table | 616/616 SR-02 rows; contained hours match Q10 | Q10 test (2 wells); full-table count is a script check | 🟡 add a count assertion |
| F-D12 | Following `report_file`, not file-name patterns | Reports with variant names still load | load check warns on unresolved reports; no dedicated test | 🟡 |
| F-D13 | Q9 compliance (SR-01..04, RC-04, RCA) derived generically | Matches Q9 expected for WELL-0001 and runs on all wells | `ground_truth/q9.py` exists; **no assertion in the suite**; not in `wellspring/` yet | ⬜ |
| F-D14 | Multiple datasets side by side (sample + test data) | Later folders add wells or replace same-ID records | `NewWellTypeAndSecondDatasetTest` (201 wells, same-ID override) | ✅ |
| F-D15 | Data quirks: nulls, missed inspections, wells drilled later | Handled without crashing or inventing values | Well with only an inspections file loads (✅). **NULL temperature is counted as downtime (bug, expected-fail test).** Profiling of real quirks still open. | 🟡 |

### 2.2 Question coverage (organizers' 18 questions)

"Data" = the product's derived data gives the expected facts (L3). "E2E" = the live copilot returns them with the right visual and evidence (L5).

| Q | What it tests | Data | E2E |
|---|---|---|---|
| Q1 | Status card: producing now, last inspection, who, why (note), stale recorded status | ✅ | ⛔ |
| Q2 | Table of non-producing wells in an area, with reasons; says "no recorded reason" rather than inventing | ✅ (wells and deltas); reasons need notes/repairs ⬜ | ⛔ |
| Q3 | Line chart of 13 readings with non-producing marked | ✅ | ⛔ |
| Q4 | Spill count and volume, corrected detection excluded | ✅ | ⛔ |
| Q5 | Downtime days in a year, with the derivation shown | ✅ | ⛔ |
| Q6 | Top-3 downtime in an area and window, bar chart | ✅ | ⛔ |
| Q7 | Map of real spills in 90 days | ✅ | ⛔ |
| Q8 | Heatmap area x month | ✅ | ⛔ |
| Q9 | Rules broken in 12 months and what is still open | ⬜ (ground truth only) | ⛔ |
| Q10 | Spills not contained within SOP time (fact only in repair reports) | ✅ | ⛔ |
| Q11 | Remembers a previous conversation | ✅ costs only; memory ⛔ | ⛔ |
| Q12 | Correct raw data, keep the original, re-answer | ⛔ overlay store | ⛔ |
| Q13 | Correct AI analysis, cascade to reportability and SOP deadlines | ⛔ | ⛔ |
| Q14 | Scoped rule for a well type, applied only to that type | ⛔ | ⛔ |
| Q15 | A correction reaches other users | ⛔ | ⛔ |
| Q16 | Shared notes visible, private memory not leaked | ⛔ | ⛔ |
| Q17 | Disagreement with data: keep both, mark unconfirmed | ⛔ | ⛔ |
| Q18 | Disagreement about the AI's analysis: show both sources | ⛔ | ⛔ |

The scored cases for all 18 are in `tests/e2e/cases.py`.

### 2.3 Agent, tools and evidence (not built yet)

| ID | Scenario | Pass criteria | Status |
|---|---|---|---|
| F-T01 | Numbers come from tool code, never LLM arithmetic | Every number in an answer traces to a tool result | ⛔ |
| F-T02 | Evidence on every claim | Each answer cites record IDs and document sections (`min_citations` in e2e cases) | ⛔ |
| F-T03 | Typed tools exist and validate input: `well_status`, `downtime`, `spills`, `repairs`, `compliance_check`, `search_docs`, `record_feedback`, `get_context`, `render` | Unit test per tool with bad input rejected | ⛔ |
| F-T04 | Unknown well, area or user | Clear "not found", no invented data | ⛔ |
| F-T05 | Ambiguous question | Asks or states the assumption | ⛔ |
| F-T06 | Document search returns cited passages | `search_docs` returns section citations; chunking is loaded | 🟡 chunks load (`doc_chunks`), no retrieval test |
| F-T07 | Model failure or rate limit | Falls back or fails gracefully with a message | ⛔ |

### 2.4 Generative UI

| ID | Scenario | Pass criteria | Status |
|---|---|---|---|
| F-U01 | Visual fits the answer: card, table, line, bar, map, heatmap | `visual` check per case in e2e | ⛔ |
| F-U02 | A named visual is honoured | e.g. Q6 returns a bar chart | ⛔ |
| F-U03 | User picker switches identity | Q15-Q17 run as different users | ⛔ |
| F-U04 | Renders on a laptop and phone width, no console errors | Manual smoke plus a browser check | ⛔ 👁 |
| F-U05 | Empty and error states | Shown, not blank | ⛔ |

### 2.5 Memory, feedback and multi-user

| ID | Scenario | Pass criteria | Status |
|---|---|---|---|
| F-M01 | Past conversations recalled by the same user | Q11 | ⛔ |
| F-M02 | Private memory never crosses users | Q16 `must_not` leak check | ⛔ |
| F-M03 | Raw-data correction keeps the original, author and reason | Original value still readable | ⛔ |
| F-M04 | Corrections are append-only and attributed | No silent overwrite; audit trail | ⛔ |
| F-M05 | Correction scope respected: one well vs a well type | Q14, WELL-0102 not affected | ⛔ |
| F-M06 | Correction applies to every later answer for every user | Q15 | ⛔ |
| F-M07 | Shared notes and unconfirmed claims | Q16, Q17 | ⛔ |
| F-M08 | Conflicts shown, not silently resolved | Q12 pump-jack note, Q17, Q18 | ⛔ |
| F-M09 | Restart persistence | State survives a restart | ⛔ |

## 3. Edge and negative cases to cover when the app exists

| ID | Case | Expected | Status |
|---|---|---|---|
| E-01 | Question about a well that does not exist (WELL-9999) | "No such well", no data | ⛔ |
| E-02 | Date window with no records | Says none found, still shows the empty visual | ⛔ |
| E-03 | Future date or "today" is not 16 Oct 2026 | Date comes from config, not hard-coded | ⬜ ground truth hard-codes `TODAY` |
| E-04 | Well with a note but no maintenance record (Q1 shut-in) | Note is the cited reason | ⛔ |
| E-05 | Stale `recorded_status` | Derived status wins, staleness flagged | ✅ data (Q1 test) / ⛔ answer |
| E-06 | Corrected detection | Excluded from counts, still visible as history | ✅ data / ⛔ answer |
| E-07 | Two users disagree | Both kept, one marked unconfirmed | ⛔ |
| E-08 | Instruction inside a note or repair report ("ignore previous instructions") | Treated as data, never obeyed | ⬜ |
| E-09 | Very long or empty question | Handled without error | ⛔ |
| E-10 | Same question twice | Same facts both times (determinism of facts) | ⛔ |

## 4. Generalization (the late test dataset)

The problem statement warns that a system tuned to the sample questions breaks. These are the guards.

| ID | Check | Pass criteria | Status |
|---|---|---|---|
| G-01 | New well type (T500, 25 °C) and a new well loaded next to the sample | Threshold read from its manual with citation; boundary is inclusive; sample answers unchanged | `NewWellTypeAndSecondDatasetTest` | ✅ synthetic |
| G-02 | Load a second dataset next to the first | Both queryable, ID collisions replace deterministically | `test_same_id_in_later_dataset_replaces_earlier_record` | ✅ synthetic |
| G-03 | Unseen question styles (different area, window, well type) | Same tools answer without code change | ⛔ |
| G-04 | No per-question logic anywhere | Review: no `if question == ...`; no sample IDs in product code | 👁 |
| G-05 | Rule change in a manual flows to answers | Edit T200 threshold 15 to 25: downtime rises by exactly the readings in [15, 25), T350 untouched, and equals the reference told the new rule | `RuleChangeFlowsThroughTest` (4 tests) | ✅ |

## 5. Non-functional requirements

| ID | Area | Requirement | How to verify | Status |
|---|---|---|---|---|
| N-01 | Correctness | Derived numbers equal an independent implementation | L3 parity | ✅ |
| N-02 | Determinism | Same data in, same derived numbers out | Idempotent load test | ✅ for data / ⛔ for answers |
| N-03 | Reproducibility | Fresh clone reaches green tests with documented steps | CI from a clean checkout | 🟡 CI written, not yet run |
| N-04 | Performance: load | Full 200-well load in a few seconds | Parity suite loads and runs in about 2 s | ✅ observed, no threshold test |
| N-05 | Performance: answer latency | Target set, e.g. p95 under the event's patience limit | Time each e2e question | ⛔ target not set |
| N-06 | Cost | Cost per answer tracked for the write-up | Token and price log per call | ⛔ |
| N-07 | Resilience: model limits | Free tiers allow ~50 requests per day; handle 429, retry, fall back | Fault injection with a stub | ⛔ |
| N-08 | Privacy | One user's memory is unreadable by another | Q16 plus API-level test | ⛔ |
| N-09 | Security: secrets | No API keys in the repo or logs | Secret scan in CI, `.gitignore` covers `.env*` | 🟡 ignore rules only |
| N-10 | Security: injection | Document and note text cannot change tool behaviour | E-08 | ⬜ |
| N-11 | Security: input handling | SQL built only from parameters; no path traversal in file reads | Code review, tests with hostile input | 🟡 loader uses parameters, tools not built |
| N-12 | Auditability | Every answer shows its evidence; every correction shows author, time, reason | F-T02, F-M04 | ⛔ |
| N-13 | Portability | Python 3.9 and 3.12 | CI matrix | 🟡 3.9 verified locally, matrix not yet run |
| N-14 | Maintainability | Rules in data, not code | F-D05, G-01, G-05 | ✅ |
| N-15 | Observability | Failures are logged with enough context to debug | Review | ⛔ |
| N-16 | Accessibility and usability | Keyboard-usable chat, readable contrast, chart alt text | Manual check | ⛔ 👁 |
| N-17 | Licensing | Public repo, MIT, organizer data fetched not copied | Repo check | ✅ |
| N-18 | Submission | Write-up (2-3 pages), video (5 min max), repo link | Checklist | 👁 ⬜ |

## 6. Release gate (done means)

1. L1-L4 green in CI on `main`.
2. L5 live run: all 10 single-user questions (Q1-Q10) pass facts, visual and evidence.
3. L5 stateful run: Q11-Q18 pass in order from a clean state.
4. L6: the perturbed-data check passes with unchanged product code.
5. N-05 latency target met, N-06 cost per answer recorded for the write-up, N-09 secret scan clean.
6. Manual demo of one question end to end in the UI recorded for the video.

## 7. Known gaps (ranked)

1. **Live app tests are blocked** until the agent loop, tools and UI exist. Everything in section 2.2 under E2E depends on it.
2. **Q9 has no automated test at all.** The ground-truth script exists but nothing asserts its output and the product does not implement it (F-D13).
3. **Generalization is proven on synthetic data only.** The real test is the organizers' dataset (section 9). The reference `ground_truth` hard-codes T200/T350 thresholds, so a new well type in their data makes the parity suite skip with a message, and a changed manual makes the producing and downtime parity tests fail until the reference is told the new rule by hand.
4. **CI has not run on GitHub yet.** Pushing a workflow file needs the `workflow` token scope; the current `gh` login has `gist, read:org, repo` only. Fix: `gh auth refresh -s workflow`.
5. **Bug: a NULL wellhead or ambient temperature is counted as not producing, i.e. 7 days of downtime** (`v_inspection` CASE falls through to ELSE 0). The sample data has no null temperatures, so no current answer is wrong, but it contradicts the design rule "unknown, never guessed". Fix: add `WHEN i.wellhead_temp_c IS NULL OR i.ambient_temp_c IS NULL THEN NULL` to the CASE in `wellspring/schema.sql`, then remove `@expectedFailure` from `test_null_reading_is_unknown_not_downtime`.
6. **Hard-coded date** in ground truth (E-03) and the Q2 "reason" column depend on notes and repairs joined in, with no test yet.
7. Non-functional targets (latency, cost per answer) are unset.

## 8. Change log

| Date | Change |
|---|---|
| 2026-10-17 | Added generalization tests (12) on synthetic and perturbed data; parity suite can target any dataset via `WELLSPRING_DATA`; found NULL-temperature bug (section 7). 56 tests. |
| 2026-10-17 | Plan created. 44 tests: unit (14), smoke (4), parity/integration (17), e2e harness (7), live e2e (2, skipped). Parity found no discrepancies between product views and ground truth. |

## 9. When the organizers' test data arrives (runbook)

Expected in the last two hours, same format as the sample. Until then, the synthetic datasets in `tests/synth.py` stand in for it.

1. Put it in `data/test-data/` (git-ignored, like the sample). Do not commit it.
2. **Load check:** `python3 -m wellspring.loader --data data/sample-data --data data/test-data --db var/wellspring.db`. Any `WARNING:` line (missing threshold for a well type, unresolved report file, orphan inspections) is a finding. Decide: data quirk to handle, or manual rule our parser missed.
3. **Parity on the new data:** `WELLSPRING_DATA=data/test-data python3 -W ignore -m unittest tests.test_parity -v`. Read the result as:
   - all pass: product and reference agree on every well in the new data;
   - skipped with "no rule for well types": a new well type, add its threshold to `ground_truth.THRESHOLD` from its manual and rerun;
   - producing or downtime parity fails: either a manual changed (update the reference by hand and confirm against the manual text) or the product has a bug. Never "fix" the reference to match the product without reading the manual.
4. **Sample answers still hold** when both datasets are loaded together: `python3 -W ignore -m unittest tests.test_generalization tests.test_parity`.
5. **Live app:** run the final questions through the copilot, record answers for page 2 of the write-up. Add the new questions to `tests/e2e/cases.py` as soon as their expected answers are known, so they become regression tests.
6. Update the statuses in this file and the change log.
