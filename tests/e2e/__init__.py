"""End-to-end tests: ask the running copilot the organizers' questions and score the answers.

Skipped until one of these is set. They call a real model, so they are slow, and billed if the provider is Claude.

  COPILOT_ADAPTER=tests.e2e.adapter:ask    in-process, on a fresh copy of the database every run (recommended)
  COPILOT_URL=http://localhost:8000        a running server (.venv/bin/python -m uvicorn wellspring.server:app);
                                           start it on a fresh copy of the database, because Q12-Q17 write feedback

The model comes from WELLSPRING_PROVIDER (default: local Ollama, free; see wellspring/llm.py). Claude is opt-in:

  Free, local:   COPILOT_ADAPTER=tests.e2e.adapter:ask python3 -m unittest tests.e2e.test_copilot -v
  Claude (paid): WELLSPRING_PROVIDER=anthropic ANTHROPIC_API_KEY=... WELLSPRING_MODEL=claude-sonnet-5-5 \
                 COPILOT_ADAPTER=tests.e2e.adapter:ask python3 -m unittest tests.e2e.test_copilot -v

scripts/run_eval.py is the quick runner for Q1-Q10 only; this package scores all 18, including the
memory, feedback and multi-user scenarios, and checks flags and evidence too.
"""
