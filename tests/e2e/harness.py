"""Answer scoring and the transport to the copilot under test."""
import importlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Turn:
    user: str
    message: str
    must: tuple = ()          # each item: a string, or a tuple of alternatives (any one is enough)
    must_not: tuple = ()
    visual: str = None        # substring of the visual type, e.g. "table"; None = not checked
    min_citations: int = 0
    new_chat: bool = False    # start a fresh conversation for this user (memory must still apply)


@dataclass
class Scenario:
    id: str
    title: str
    turns: list = field(default_factory=list)


def norm(text):
    t = str(text).lower().replace("−", "-").replace("°", " ")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)      # 25,794 -> 25794
    t = re.sub(r"usd\s*|\$", "", t)
    return re.sub(r"\s+", " ", t)


def score(turn, reply):
    """Return a list of failure messages (empty = pass)."""
    text = norm(reply.get("answer", "") + " " + " ".join(map(str, reply.get("citations", []))))
    fails = []
    for need in turn.must:
        options = need if isinstance(need, tuple) else (need,)
        if not any(norm(o) in text for o in options):
            fails.append(f"missing fact: {' | '.join(options)}")
    for bad in turn.must_not:
        if norm(bad) in text:
            fails.append(f"must not appear: {bad}")
    if turn.visual:
        v = reply.get("visual") or {}
        vt = norm(v.get("type", "") if isinstance(v, dict) else v)
        if turn.visual not in vt:
            fails.append(f"visual: expected '{turn.visual}', got '{vt or 'none'}'")
    if len(reply.get("citations") or []) < turn.min_citations:
        fails.append(f"evidence: expected >= {turn.min_citations} citations, got {len(reply.get('citations') or [])}")
    return fails


def to_reply(answer):
    """Shape a copilot Answer (text, visual, evidence, flags) for scoring.
    Flags are shown to the user next to the text, so their messages count as part of the answer."""
    flags = " ".join(f"[{f['kind']}] {f['message']}" for f in answer.get("flags") or [])
    return {"answer": (answer.get("text") or "") + " " + flags,
            "visual": answer.get("visual") or {},
            "citations": [e["ref"] for e in answer.get("evidence") or []]}


def connect():
    """Return (ask, reset) for the configured copilot, or None when none is configured.

    COPILOT_ADAPTER=pkg.module:callable   ask(user_name, message, session) -> {"answer","visual","citations"};
                                          the module may also define reset()
    COPILOT_URL=http://localhost:8000     the real HTTP API: GET /api/users, POST /api/ask
    """
    spec = os.environ.get("COPILOT_ADAPTER")
    if spec:
        mod_name, fn = spec.split(":")
        mod = importlib.import_module(mod_name)
        return getattr(mod, fn), getattr(mod, "reset", lambda: None)
    url = os.environ.get("COPILOT_URL")
    if not url:
        return None
    base = url.rstrip("/")

    def call(path, body=None):
        req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=int(os.environ.get("COPILOT_TIMEOUT", "180"))) as r:
            return json.load(r)

    ids = {u["name"]: u["user_id"] for u in call("/api/users")}
    conversations = {}

    def ask(user, message, session):
        body = {"user_id": ids[user], "question": message}
        if session in conversations:
            body["conversation_id"] = conversations[session]
        answer = call("/api/ask", body)
        conversations[session] = answer["conversation_id"]
        return to_reply(answer)

    # The HTTP server keeps its state in its database; start it on a fresh copy of var/wellspring.db for a clean run.
    return ask, (lambda: conversations.clear())
