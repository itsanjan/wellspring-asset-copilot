# Final two hours: runbook

The organizers release a **test dataset** (same format, more wells and history), then the **final questions**.
Everything below is one command per step. Rehearsed with the sample plus a synthetic extra dataset
(new well, new well type T500 with its own manual): loads, parses the new threshold, 0 tool errors.

## 1. Get the data (2 min)

Put the test dataset next to the sample, unchanged:

```
data/sample-data/      # already there (scripts/fetch_data.sh)
data/test-data/        # unzip / clone the release here: it must contain fleet/ and assets/
```

## 2. Load both datasets, fresh (1 min)

```
.venv/bin/python -m wellspring.loader --data data/sample-data --data data/test-data --db var/wellspring.db --fresh
```

- `--fresh` drops corrections, notes and conversations made while building, so final answers start from the data.
  Leave it out to keep them.
- Later folders win on duplicate IDs; new wells, areas, users, manuals and SOPs are picked up automatically.
- Read the report it prints:
  - **today**: defaults to the day after the latest inspection. If the release states another "today", reload
    with `--today YYYY-MM-DD`.
  - **every well type has a producing threshold** (a `MISSING` line means a manual the parser could not read: open
    the manual, find the rule, and either fix the regex in `wellspring/rules.py` or tell the copilot the rule).
  - **SOP rules**: all ten present.
  - Any `WARNING:` lines.

## 3. Smoke-check without the model (1 min)

```
.venv/bin/python scripts/check_dataset.py
```

Runs every tool over every area and 25 random wells. Expect `0 errors`. Eyeball the headline numbers.

## 4. Start the copilot (1 min)

Ollama must be running with `gemma4:12b-32k` (see README). Then:

```
.venv/bin/python -m uvicorn wellspring.server:app --port 8000      # http://localhost:8000
```

The header shows the model in use; a yellow banner means the model is not reachable.

## 5. Run the final questions

- Ask each question in the UI as the user it names (user picker); start a **new chat** when a question says so.
- Scenarios that follow each other (corrections, then questions that depend on them) must run in the given order
  in the same database, as in `scripts/run_scenarios.py`.
- Each answer is saved with its visual, evidence, flags and cost; reopen it from the sidebar for the video.
- Budget about 2-5 minutes per question on the local model. If time is short, run the rest in a terminal while
  recording the video:

```
.venv/bin/python -m wellspring.chat --user U-201 "question text"
```

## 6. Record results for page 2

For each final question note: the answer, the visual, correct or not, and why (tool chosen, data, rule).
The JSON answer object (`--json` flag on `wellspring.chat`, or `answer_json` in the `turns` table) has
everything: tools used, evidence, flags, tokens.

## If something breaks

| Symptom | Fix |
|---|---|
| `producing status will be unknown` for a type | New manual words the rule differently: adjust `PRODUCING` regex in `wellspring/rules.py`, reload |
| `SOP rule ... not found` | Same, `SOP_PATTERNS` in `wellspring/rules.py` |
| Report facts missing (`report file not found`) | Check `report_file` paths in the new `maintenance.jsonl` |
| Banner: model not available | Start the Ollama app; `ollama ps`; `ollama run gemma4:12b-32k "hi"` |
| Answers very slow | Close other apps (the model needs about 8 GB); or `WELLSPRING_PROVIDER=gemini` with a free `GEMINI_API_KEY` |
