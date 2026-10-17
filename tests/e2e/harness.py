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


def connect():
    """Return (ask, reset) for the configured copilot, or None when none is configured."""
    spec = os.environ.get("COPILOT_ADAPTER")
    if spec:
        mod, fn = spec.split(":")
        return getattr(importlib.import_module(mod), fn), (lambda: None)
    url = os.environ.get("COPILOT_URL")
    if not url:
        return None

    def post(path, body):
        req = urllib.request.Request(url.rstrip("/") + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=int(os.environ.get("COPILOT_TIMEOUT", "180"))) as r:
            return json.load(r)

    def reset():
        try:
            post("/api/reset", {})
        except Exception:
            pass

    return (lambda user, message, session: post("/api/chat", {"user": user, "message": message, "session": session})), reset
