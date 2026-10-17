# Wellspring: Asset Intelligence Copilot

A chat copilot that answers oil-field operators' plain-English questions about their wells,
correctly and with cited evidence, and shows each answer with a fitting inline visual.
Built for the FlytBase Hack Day (Hacktoberfest 2026).

The LLM decides what to ask; deterministic tools decide what is true. See
[`CONCEPT.md`](CONCEPT.md) for the problem, architecture, build steps and todos.

## Status

Early stage. The ground-truth script reproduces the expected answers for sample questions Q1-Q8.

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

# Web UI (chat, user picker, inline visuals, citations)
(cd web && npm install && npm run build)
.venv/bin/python -m uvicorn wellspring.server:app --port 8000   # open http://localhost:8000
# for UI development: run the server as above, then `cd web && npm run dev` (http://localhost:5173, proxies /api)
```

## Layout

| Path | What |
|---|---|
| `problem-statement/` | Organizer's challenge documents, copied as-is for reference |
| `CONCEPT.md` | Proposal and architecture |
| `docs/DIAGRAMS.md` | Flows and diagrams (Mermaid) to verify the design |
| `docs/QA-VALIDATION-PLAN.md` | QA plan: scenarios, functional and non-functional checks, with status tracking |
| `tests/` | Unit, parity, and end-to-end tests (`python3 -m unittest discover -s tests -t .`) |
| `wellspring/` | Product code: loader, rule parser, SQLite schema and views, overlay, tools, agent, CLI chat, HTTP API |
| `web/` | React chat UI (Vite + TypeScript, Recharts, Leaflet) |
| `ground_truth/` | LLM-free ground-truth calculations used as tests |
| `scripts/` | Helper scripts (data fetch) |
| `data/` | Local dataset (git-ignored) |

## Data

The sample dataset belongs to the challenge organizers
([source](https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset))
and is synthetic. It is fetched, not redistributed here.

## License

[MIT](LICENSE)
