"""HTTP API: validation, error mapping, shared feedback, and the privacy rules for conversations."""
import os
import unittest
from unittest import mock

from . import dbfix
from .fakes import FakeClient, present, reply, tool

server = client = Copilot = None
SKIP_REASON = None


def setUpModule():
    global server, client, Copilot, SKIP_REASON
    if not dbfix.have_data():
        raise unittest.SkipTest("run scripts/fetch_data.sh first")
    patcher = mock.patch.dict(os.environ, {"WELLSPRING_DB": dbfix.copy_db(),
                                           "WELLSPRING_PROVIDER": "anthropic"})  # scripted client = Anthropic-style backend
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    try:
        from fastapi.testclient import TestClient
        from wellspring import server as srv
        from wellspring.agent import Copilot as Cp
    except Exception as err:  # fastapi/httpx/anthropic missing, or Python too old for the pydantic models
        raise unittest.SkipTest(f"server dependencies unavailable: {err}")
    server, Copilot, client = srv, Cp, TestClient(srv.app)


def script(*extra):
    return FakeClient(reply(tool("t1", "well_status", asset_ids=["WELL-0001"])),
                      reply(present("p1", "WELL-0001 is not producing (INS-0001-0203).", "card", "R1")), *extra)


def use(fake, ready=True):
    """Install a copilot over a scripted model. `ready` is what the backend reports to the server (a real
    Anthropic backend reports not-ready without a key)."""
    server._copilot = Copilot(server.DB, client=fake)
    server._copilot.backend.ready = lambda: (ready, "" if ready else "set ANTHROPIC_API_KEY")
    return fake


class HealthAndDirectoryTest(unittest.TestCase):
    def test_health_reports_wells_and_model_readiness(self):
        use(script())
        body = client.get("/api/health").json()
        self.assertEqual((body["ok"], body["wells"], body["model_ready"]), (True, 200, True))
        self.assertEqual(body["provider"], "anthropic")

    def test_health_says_why_the_model_is_not_ready_and_never_leaks_the_key(self):
        use(script(), ready=False)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "SENTINEL-do-not-leak"}):
            r = client.get("/api/health")
        self.assertFalse(r.json()["model_ready"])
        self.assertTrue(r.json()["model_detail"])
        self.assertNotIn("SENTINEL", r.text)

    def test_users_include_roles_and_teams(self):
        users = {u["user_id"]: u for u in client.get("/api/users").json()}
        self.assertEqual(users["U-201"]["name"], "Michael Turner")
        self.assertEqual(users["U-401"]["role"], "hse_officer")


class AskTest(unittest.TestCase):
    def post(self, **body):
        return client.post("/api/ask", json={"user_id": "U-201", "question": "Is WELL-0001 producing oil right now?", **body})

    def test_answer_has_the_fields_the_ui_needs(self):
        use(script())
        r = self.post()
        self.assertEqual(r.status_code, 200)
        a = r.json()
        for key in ("text", "visual", "evidence", "flags", "tools_used", "usage", "conversation_id"):
            self.assertIn(key, a)
        self.assertEqual(a["visual"]["type"], "card")

    def test_validation_rejects_empty_and_oversized_questions_and_unknown_users(self):
        fake = use(script())
        self.assertEqual(self.post(question="").status_code, 422)
        self.assertEqual(self.post(question="x" * 4001).status_code, 422)
        self.assertEqual(self.post(user_id="U-999").status_code, 404)
        self.assertEqual(client.post("/api/ask", json={"question": "hi"}).status_code, 422)
        self.assertEqual(fake.calls, [], "invalid requests must never reach the model")

    def test_model_not_available_is_a_clear_503_and_the_model_is_not_called(self):
        fake = use(script(), ready=False)
        r = self.post()
        self.assertEqual(r.status_code, 503)
        self.assertIn("ANTHROPIC_API_KEY", r.json()["detail"])
        self.assertEqual(fake.calls, [])

    def test_provider_rejecting_the_key_after_readiness_is_a_502_naming_the_error(self):
        class AuthenticationError(Exception):
            pass
        use(FakeClient(raises=AuthenticationError("invalid x-api-key")))
        r = self.post()
        self.assertEqual(r.status_code, 502)
        self.assertIn("AuthenticationError", r.json()["detail"])

    def test_other_model_failures_become_502_with_the_error_name(self):
        use(FakeClient(raises=RuntimeError("upstream timeout")))
        r = self.post()
        self.assertEqual(r.status_code, 502)
        self.assertIn("RuntimeError", r.json()["detail"])


class PrivacyTest(unittest.TestCase):
    """Q16: shared knowledge is readable by everyone, private conversations only by their owner."""

    @classmethod
    def setUpClass(cls):
        cls.fake = use(script())
        cls.conv = client.post("/api/ask", json={"user_id": "U-201", "question": "Is WELL-0001 producing oil right now?"}).json()["conversation_id"]

    def test_owner_can_list_and_read_their_conversation(self):
        listed = client.get("/api/conversations", params={"user_id": "U-201"}).json()
        self.assertIn(self.conv, [c["conversation_id"] for c in listed])
        turns = client.get(f"/api/conversations/{self.conv}", params={"user_id": "U-201"}).json()["turns"]
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        self.assertEqual(turns[1]["answer"]["visual"]["type"], "card")

    def test_other_user_cannot_list_or_read_it(self):
        self.assertEqual(client.get("/api/conversations", params={"user_id": "U-202"}).json(), [])
        r = client.get(f"/api/conversations/{self.conv}", params={"user_id": "U-202"})
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("WELL-0001", r.text)

    def test_other_user_cannot_continue_it_and_the_model_is_not_called(self):
        before = len(self.fake.calls)
        r = client.post("/api/ask", json={"user_id": "U-202", "question": "what did we discuss?", "conversation_id": self.conv})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.fake.calls), before)

    def test_unknown_user_gets_404_on_conversation_endpoints(self):
        self.assertEqual(client.get("/api/conversations", params={"user_id": "U-999"}).status_code, 404)


class SharedFeedbackTest(unittest.TestCase):
    def test_feedback_is_visible_to_everyone_with_author_and_original_value(self):
        from wellspring import tools
        con = dbfix.connect(server.DB)
        tools.run(tools.Context(con, "U-201"), "record_correction",
                  {"inspection_id": "INS-0164-0205", "field": "wellhead_temp_c", "new_value": "41.0", "reason": "faulty gun"})
        con.close()
        entries = client.get("/api/feedback").json()
        mine = next(e for e in entries if e["target_ref"] == "INS-0164-0205")
        self.assertEqual((mine["author"], mine["original_value"], mine["new_value"], mine["kind"]),
                         ("Michael Turner", "24.0", "41.0", "correction"))


if __name__ == "__main__":
    unittest.main()
