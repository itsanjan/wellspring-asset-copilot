"""Model backends. The agent loop is provider-neutral; each backend speaks one API.

Pick one with WELLSPRING_PROVIDER (default "ollama", whatever API keys are in the environment):

  ollama      free, local, no key, no rate limit   model default gemma4:12b-32k (gemma4:12b with a 32K context)
  openrouter  free tier (about 50 requests/day)    OPENROUTER_API_KEY, model default google/gemma-4-31b-it:free
  gemini      Google AI Studio free tier           GEMINI_API_KEY, model default gemma-4-31b-it
  anthropic   paid, opt-in only                    ANTHROPIC_API_KEY, model default claude-opus-5-5

WELLSPRING_MODEL overrides the model for any provider.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

# USD per million tokens: input, output, cache write, cache read. Unknown models (local/free) cost 0.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
          "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01)}

OPENAI_COMPAT = {
    "ollama": {"base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), "key_env": None, "model": "gemma4:12b-32k"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "model": "google/gemma-4-31b-it:free"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY", "model": "gemma-4-31b-it"},
}


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    error: str | None = None  # set when the model's arguments were not valid JSON


@dataclass
class Step:
    text: str = ""
    calls: list = field(default_factory=list)
    refused: bool = False
    usage: dict = field(default_factory=dict)


def cost(model, usage):
    p = PRICES.get(model)
    if not p:
        return 0.0
    return round((usage.get("input_tokens", 0) * p[0] + usage.get("output_tokens", 0) * p[1]
                  + usage.get("cache_write_tokens", 0) * p[2] + usage.get("cache_read_tokens", 0) * p[3]) / 1e6, 4)


class AnthropicBackend:
    provider = "anthropic"
    compact = False  # large context: tool results go in full

    def __init__(self, model=None, effort=None, client=None):
        self.model = model or os.environ.get("WELLSPRING_MODEL", "claude-opus-5-5")
        self.effort = effort or os.environ.get("WELLSPRING_EFFORT", "medium")
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def start(self, system, history, question):
        return {"system": [{"type": "text", "text": system}], "messages": list(history) + [{"role": "user", "content": question}]}

    def step(self, state, tools):
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=16000, system=state["system"], messages=state["messages"],
            tools=tools, output_config={"effort": self.effort}, cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        u = r.usage
        usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                 "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
        if r.stop_reason == "refusal":
            return Step(refused=True, usage=usage)
        state["messages"].append({"role": "assistant", "content": r.content})
        return Step(text="".join(b.text for b in r.content if b.type == "text"),
                    calls=[ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"], usage=usage)

    def add_results(self, state, results):
        state["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": content, **({"is_error": True} if err else {})}
            for cid, content, err in results]})

    def ready(self):
        ok = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return ok, "" if ok else "set ANTHROPIC_API_KEY"


class OpenAICompatBackend:
    """Ollama, OpenRouter and Google AI Studio all expose the OpenAI chat-completions API with tool calling."""
    compact = True  # small/free models: send trimmed tool results

    def __init__(self, provider="ollama", model=None, client=None):
        cfg = OPENAI_COMPAT[provider]
        self.provider, self.base_url = provider, cfg["base_url"]
        self.model = model or os.environ.get("WELLSPRING_MODEL", cfg["model"])
        self.key_env = cfg["key_env"]
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=self.base_url, api_key=os.environ.get(self.key_env, "") if self.key_env else "ollama",
                            timeout=600)
        self.client = client

    @staticmethod
    def _tool(t):
        return {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}

    def start(self, system, history, question):
        return {"messages": [{"role": "system", "content": system}] + list(history) + [{"role": "user", "content": question}]}

    def step(self, state, tools):
        r = self.client.chat.completions.create(model=self.model, messages=state["messages"],
                                                tools=[self._tool(t) for t in tools], temperature=0.2)
        m = r.choices[0].message
        calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {}))
            except json.JSONDecodeError as err:
                calls.append(ToolCall(tc.id, tc.function.name, {}, error=f"arguments were not valid JSON: {err}"))
        state["messages"].append({"role": "assistant", "content": m.content or "",
                                  **({"tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                                     for tc in m.tool_calls]} if m.tool_calls else {})})
        u = r.usage
        return Step(text=m.content or "", calls=calls,
                    usage={"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0})

    def add_results(self, state, results):
        for cid, content, _err in results:
            state["messages"].append({"role": "tool", "tool_call_id": cid, "content": content})

    def ready(self):
        if self.provider == "ollama":
            try:
                with urllib.request.urlopen(self.base_url.replace("/v1", "") + "/api/tags", timeout=2) as resp:
                    names = [m["name"] for m in json.load(resp)["models"]]
            except OSError:
                return False, "Ollama is not running (start the Ollama app or `ollama serve`)"
            base = self.model if ":" in self.model else self.model + ":latest"
            return (base in names), ("" if base in names else f"model {self.model} not pulled; run `ollama pull {self.model}`")
        ok = bool(os.environ.get(self.key_env))
        return ok, "" if ok else f"set {self.key_env}"


def make_backend(client=None):
    """client: an Anthropic-style client injected by tests to script the model; it selects that backend."""
    provider = os.environ.get("WELLSPRING_PROVIDER", "anthropic" if client is not None else "ollama").lower()
    if provider == "anthropic":
        return AnthropicBackend(client=client)
    if provider in OPENAI_COMPAT:
        return OpenAICompatBackend(provider, client=client)
    raise ValueError(f"unknown WELLSPRING_PROVIDER {provider!r}; choose ollama, openrouter, gemini or anthropic")
