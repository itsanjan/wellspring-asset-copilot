# Wellspring: Asset Intelligence Copilot

A chat copilot that answers oil-field operators' plain-English questions about their wells,
correctly and with cited evidence, and shows each answer with a fitting inline visual.
Built for the FlytBase Hack Day (Hacktoberfest 2026).

The LLM decides what to ask; deterministic tools decide what is true. See
[`CONCEPT.md`](CONCEPT.md) for the problem, architecture, build steps and todos.

## Status

Working end to end: loader, SQLite store with derived views, a shared overlay (corrections, scoped rules,
notes, claims), 15 typed tools, a provider-neutral agent loop, an HTTP API and a React chat UI.

- **Verified without a model:** the tool layer reproduces the organizers' expected facts for Q1-Q18, and the
  product agrees with an independent ground-truth implementation on all 200 wells (136 automated tests, run in CI).
- **Not yet verified:** how real models answer. First live check on the default local model (Gemma 4 12B):
  Q1 passed all 5 expected facts with the right visual, in about 4 minutes. Q2-Q18 are not scored yet.
- **Known bugs:** two, both pinned by tests and listed in the QA plan (a NULL temperature counts as downtime;
  the agent, but not the HTTP API, lets a second user continue another user's conversation).

See [`docs/QA-VALIDATION-PLAN.md`](docs/QA-VALIDATION-PLAN.md) for the full, status-tracked list.
**Latest test report (updated on every push to `main`): https://itsanjan.github.io/wellspring-asset-copilot/**

## Quick start

```
scripts/fetch_data.sh          # downloads the organizer's sample dataset into data/
python3 -I ground_truth/ground_truth.py    # recomputes Q1-Q8 straight from the raw files
python3 -m wellspring.loader --data data/sample-data --db var/wellspring.db   # build the SQLite store (about 1 s)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt

# Model: free and local by default (Ollama + Gemma 4). One-time setup:
ollama pull gemma4:12b
printf 'FROM gemma4:12b\nPARAMETER num_ctx 32768\n' > /tmp/Modelfile && ollama create gemma4:12b-32k -f /tmp/Modelfile
# Other providers: WELLSPRING_PROVIDER=openrouter|gemini|anthropic plus its key (see wellspring/llm.py)

.venv/bin/python -m wellspring.chat --user U-201 "Is WELL-0001 producing oil right now? Show me its status card."
.venv/bin/python scripts/run_eval.py   # Q1-Q10 against the expected facts and visuals
.venv/bin/python scripts/run_scenarios.py   # Q11-Q18: memory, corrections, multiple users
.venv/bin/python scripts/check_dataset.py   # smoke-check a loaded dataset, no model needed

# Web UI (chat, user picker, inline visuals, citations)
(cd web && npm install && npm run build)
.venv/bin/python -m uvicorn wellspring.server:app --port 8000   # open http://localhost:8000
# for UI development: run the server as above, then `cd web && npm run dev` (http://localhost:5173, proxies /api)
```

## Testing

```
python3 -m unittest discover -s tests -t .      # everything below, under a minute; needs data/sample-data and .venv for the app tests
WELLSPRING_DATA=data/test-data python3 -m unittest tests.test_parity -v   # cross-check the product against ground truth on another dataset
```

| Layer | Files | Needs a model |
|---|---|---|
| Rule parsing, ground-truth smoke, product-vs-ground-truth parity | `test_rules`, `test_ground_truth`, `test_parity` | no |
| Tools (Q1-Q18 facts, corrections, scoped rules, claims, private memory) | `test_tools` | no |
| Agent loop, model backends, HTTP API | `test_agent`, `test_llm`, `test_server` | no (scripted model) |
| Generalization: changed manual, new well type, second dataset (synthetic) | `test_generalization`, `synth.py` | no |
| End-to-end scorer and a scripted full-pipeline case | `tests/e2e/test_harness`, `test_pipeline` | no |
| **Live end to end, Q1-Q18** | `tests/e2e/test_copilot` | **yes**, manual |

```
# Live end to end (free, local Gemma by default; add WELLSPRING_PROVIDER=anthropic and a key for Claude)
COPILOT_ADAPTER=tests.e2e.adapter:ask .venv/bin/python -m unittest tests.e2e.test_copilot -v
.venv/bin/python scripts/run_eval.py --only Q1,Q5      # quick Q1-Q10 check, writes var/eval-<provider>-<model>.json
```

GitHub Actions runs the model-free tests on every push (Python 3.12 full, Python 3.9 data layer) and publishes the
results and the QA plan to GitHub Pages: [https://itsanjan.github.io/wellspring-asset-copilot/](https://itsanjan.github.io/wellspring-asset-copilot/). To build the same report locally:
`.venv/bin/python scripts/test_report.py --out site`, then open `site/index.html`.

## Layout

| Path | What |
|---|---|
| `problem-statement/` | Organizer's challenge documents, copied as-is for reference |
| `CONCEPT.md` | Proposal and architecture |
| `docs/FINAL-HOURS.md` | Runbook for loading the test dataset and answering the final questions |
| `docs/DIAGRAMS.md` | Flows and diagrams (Mermaid) to verify the design |
| `docs/QA-VALIDATION-PLAN.md` | QA plan: scenarios, functional and non-functional checks, with status tracking (also published on the test-report site) |
| `tests/` | Unit, parity, tool, agent, backend, server, generalization and end-to-end tests (see Testing) |
| `wellspring/` | Product code: loader, rule parser, SQLite schema and views, overlay, tools, agent, CLI chat, HTTP API |
| `web/` | React chat UI (Vite + TypeScript, Recharts, Leaflet) |
| `ground_truth/` | LLM-free ground-truth calculations used as tests |
| `scripts/` | `fetch_data.sh` (organizer dataset), `run_eval.py` (Q1-Q10 against a real model), `test_report.py` (HTML test report for GitHub Pages) |
| `data/` | Local dataset (git-ignored) |

## Data

The sample dataset belongs to the challenge organizers
([source](https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset))
and is synthetic. It is fetched, not redistributed here.

## License

[MIT](LICENSE)
