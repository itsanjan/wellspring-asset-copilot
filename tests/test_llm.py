"""wellspring/llm.py: provider selection, cost, and the OpenAI-compatible backend (Ollama, OpenRouter, Gemini)
that free local models use. All with scripted clients: no network, no model."""
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from . import dbfix

try:
    from wellspring import llm
    from wellspring.agent import Copilot
except ImportError as err:
    llm = None
    IMPORT_ERROR = err


def setUpModule():
    if llm is None:
        raise unittest.SkipTest(f"app dependencies missing: {IMPORT_ERROR}")


class FakeOpenAI:
    """client.chat.completions.create(...) returns scripted OpenAI-style completions in order."""

    def __init__(self, *messages):
        self.messages, self.calls = list(messages), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(dict(kw, messages=list(kw["messages"])))
        return NS(choices=[NS(message=self.messages.pop(0))], usage=NS(prompt_tokens=120, completion_tokens=30))


def call(id, name, arguments):
    return NS(id=id, function=NS(name=name, arguments=arguments if isinstance(arguments, str) else json.dumps(arguments)))


def msg(content="", *calls):
    return NS(content=content, tool_calls=list(calls) or None)


class ProviderSelectionTest(unittest.TestCase):
    def pick(self, env, client=None):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(llm, "AnthropicBackend") as a, mock.patch.object(llm, "OpenAICompatBackend") as o:
            llm.make_backend(client)
            return a, o

    def test_default_is_free_local_ollama(self):
        a, o = self.pick({})
        a.assert_not_called()
        o.assert_called_once_with("ollama", client=None)

    def test_an_anthropic_key_alone_does_not_switch_to_paid_claude(self):
        """Claude is opt-in: a key lying around in the shell must not start billing calls."""
        a, o = self.pick({"ANTHROPIC_API_KEY": "k"})
        a.assert_not_called()
        o.assert_called_once_with("ollama", client=None)

    def test_claude_needs_an_explicit_provider(self):
        a, o = self.pick({"ANTHROPIC_API_KEY": "k", "WELLSPRING_PROVIDER": "anthropic"})
        a.assert_called_once_with(client=None)
        o.assert_not_called()

    def test_an_injected_client_means_the_anthropic_style_backend(self):
        a, _ = self.pick({}, client=object())
        a.assert_called_once()

    def test_explicit_provider_wins_even_with_an_injected_client(self):
        _, o = self.pick({"WELLSPRING_PROVIDER": "openrouter"}, client=object())
        o.assert_called_once_with("openrouter", client=unittest.mock.ANY)

    def test_provider_name_is_case_insensitive(self):
        _, o = self.pick({"WELLSPRING_PROVIDER": "Gemini"})
        o.assert_called_once_with("gemini", client=None)

    def test_unknown_provider_is_rejected_with_the_valid_choices(self):
        with mock.patch.dict(os.environ, {"WELLSPRING_PROVIDER": "bogus"}, clear=True):
            with self.assertRaisesRegex(ValueError, "ollama, openrouter, gemini or anthropic"):
                llm.make_backend()


class CostTest(unittest.TestCase):
    def test_known_model_cost_uses_all_four_token_prices(self):
        u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_write_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
        self.assertEqual(llm.cost("claude-opus-5-5", u), 4.0 + 20.0 + 5.0 + 0.20)
        self.assertEqual(llm.cost("claude-sonnet-5-5", u), 2.0 + 10.0 + 2.5 + 0.20)

    def test_local_and_free_models_cost_nothing(self):
        for model in ("gemma4:12b-32k", "google/gemma-4-31b-it:free", "gemma-4-31b-it"):
            self.assertEqual(llm.cost(model, {"input_tokens": 10**6, "output_tokens": 10**6}), 0.0, model)


class OpenAICompatBackendTest(unittest.TestCase):
    def backend(self, *messages, provider="ollama"):
        fake = FakeOpenAI(*messages)
        return llm.OpenAICompatBackend(provider, client=fake), fake

    def test_defaults_per_provider(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.backend()[0].model, "gemma4:12b-32k")
            self.assertEqual(self.backend(provider="openrouter")[0].model, "google/gemma-4-31b-it:free")
        with mock.patch.dict(os.environ, {"WELLSPRING_MODEL": "my-model"}):
            self.assertEqual(self.backend()[0].model, "my-model")

    def test_tool_calls_are_parsed_and_tool_definitions_converted(self):
        b, fake = self.backend(msg("", call("c1", "well_status", {"asset_ids": ["WELL-0001"]})))
        state = b.start("SYSTEM", [], "question")
        step = b.step(state, [{"name": "well_status", "description": "d", "input_schema": {"type": "object", "properties": {}}}])
        self.assertEqual([(c.name, c.input) for c in step.calls], [("well_status", {"asset_ids": ["WELL-0001"]})])
        self.assertEqual(step.usage, {"input_tokens": 120, "output_tokens": 30})
        sent = fake.calls[0]
        self.assertEqual(sent["tools"][0]["function"]["name"], "well_status")
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])

    def test_invalid_json_arguments_become_an_error_for_the_model_not_a_crash(self):
        b, _ = self.backend(msg("", call("c1", "well_status", "{not json")))
        step = b.step(b.start("S", [], "q"), [])
        self.assertIn("not valid JSON", step.calls[0].error)
        self.assertEqual(step.calls[0].input, {})

    def test_non_object_arguments_are_treated_as_empty(self):
        b, _ = self.backend(msg("", call("c1", "well_status", "[1, 2]")))
        self.assertEqual(b.step(b.start("S", [], "q"), []).calls[0].input, {})

    def test_assistant_tool_call_and_tool_result_are_threaded_back_in_order(self):
        b, fake = self.backend(msg("", call("c1", "well_status", {})), msg("done"))
        state = b.start("S", [], "q")
        b.step(state, [])
        b.add_results(state, [("c1", "RESULT", False)])
        b.step(state, [])
        roles = [m["role"] for m in fake.calls[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool"])
        self.assertEqual(fake.calls[1]["messages"][-1], {"role": "tool", "tool_call_id": "c1", "content": "RESULT"})

    def test_ollama_readiness_explains_what_to_do(self):
        b, _ = self.backend()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            ok, detail = b.ready()
        self.assertFalse(ok)
        self.assertIn("Ollama is not running", detail)

    def test_ollama_readiness_detects_a_model_that_was_not_pulled(self):
        b, _ = self.backend()
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps({"models": [{"name": "other:latest"}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=resp), mock.patch("json.load", return_value={"models": [{"name": "other:latest"}]}):
            ok, detail = b.ready()
        self.assertFalse(ok)
        self.assertIn("ollama pull gemma4:12b-32k", detail)

    def test_hosted_free_providers_need_their_key(self):
        b, _ = self.backend(provider="openrouter")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(b.ready(), (False, "set OPENROUTER_API_KEY"))
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
            self.assertEqual(b.ready(), (True, ""))

    def test_anthropic_backend_readiness_needs_a_key(self):
        b = llm.AnthropicBackend(client=object())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(b.ready(), (False, "set ANTHROPIC_API_KEY"))


@unittest.skipUnless(dbfix.have_data(), "run scripts/fetch_data.sh first")
class SmallModelThroughTheAgentTest(unittest.TestCase):
    """A free local model through the whole agent: compact tool results, visual chosen from the question,
    and a model that never calls present_answer still produces an answer."""

    def run_agent(self, *messages, question="Is WELL-0001 producing oil right now? Show me its status card."):
        fake = FakeOpenAI(*messages)
        cp = Copilot(dbfix.copy_db(), backend=llm.OpenAICompatBackend("ollama", client=fake))
        return cp.ask("U-201", question), fake

    def test_plain_text_answer_gets_the_visual_the_question_names(self):
        a, _ = self.run_agent(msg("", call("c1", "well_status", {"asset_ids": ["WELL-0001"]})),
                              msg("WELL-0001 is not producing (INS-0001-0203)."))
        self.assertEqual(a["text"], "WELL-0001 is not producing (INS-0001-0203).")
        self.assertEqual(a["visual"]["type"], "card")
        self.assertEqual(a["usage"]["provider"], "ollama")
        self.assertEqual(a["usage"]["cost_usd"], 0.0)

    def test_heat_map_wording_in_the_question_selects_a_heatmap(self):
        a, _ = self.run_agent(msg("", call("c1", "spills", {"start": "2026-01-01", "end": "2026-10-16", "group_by": "area_month"})),
                              msg("Here you go."), question="Show a heat map of 2026 spills by area and month.")
        self.assertEqual(a["visual"]["type"], "heatmap")

    def test_tool_results_are_compacted_for_small_models(self):
        _, fake = self.run_agent(msg("", call("c1", "well_status", {"asset_ids": ["WELL-0001"]})), msg("ok"))
        content = json.loads(fake.calls[1]["messages"][-1]["content"])
        self.assertTrue(all(set(e) == {"kind", "ref"} for e in content["evidence"]))
        self.assertTrue(all(v not in (None, "", [], {}) for row in content["rows"] for v in row.values()))
        self.assertIn("summary", content)

    def test_long_results_are_truncated_with_a_note_and_the_summary_keeps_totals(self):
        _, fake = self.run_agent(msg("", call("c1", "spills", {"start": "2026-01-01", "end": "2026-10-16"})), msg("ok"))
        content = json.loads(fake.calls[1]["messages"][-1]["content"])
        self.assertEqual(len(content["rows"]), 40)
        self.assertIn("showing 40 of 141 rows", content["note"])
        self.assertEqual(content["summary"]["spills"], 141)

    def test_bad_json_arguments_reach_the_model_as_a_tool_error_and_it_can_recover(self):
        a, fake = self.run_agent(msg("", call("c1", "well_status", "{oops")), msg("Sorry, retrying without it."))
        tool_msg = fake.calls[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("Error:", tool_msg["content"])
        self.assertEqual(a["text"], "Sorry, retrying without it.")


if __name__ == "__main__":
    unittest.main()
