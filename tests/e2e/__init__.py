"""End-to-end tests: ask the running copilot the organizers' questions and score the answers.

The app does not exist yet, so these tests are skipped until one of these is set:

  COPILOT_URL=http://localhost:8000      POST {url}/api/chat  {"user", "message", "session"}
                                         -> {"answer": str, "visual": {"type": str}, "citations": [str]}
                                         optional POST {url}/api/reset before the run
  COPILOT_ADAPTER=pkg.module:callable    callable(user, message, session) -> the same dict

Run: COPILOT_URL=... python3 -m unittest tests.e2e.test_copilot -v
"""
