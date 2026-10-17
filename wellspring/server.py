"""HTTP API for the chat UI, and the built UI itself.

    .venv/bin/python -m uvicorn wellspring.server:app --port 8000

Private data (conversations) is always filtered by the user_id of the request; there is no way to
list or read another user's conversations. Shared knowledge (the overlay) is readable by everyone.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB = os.environ.get("WELLSPRING_DB", "var/wellspring.db")
WEB = os.path.join(os.path.dirname(__file__), "..", "web", "dist")

app = FastAPI(title="Wellspring")
_copilot = None


def copilot():
    global _copilot
    if _copilot is None:
        from .agent import Copilot  # imported lazily so the read-only API works without a model
        _copilot = Copilot(DB)
    return _copilot


def q(sql, params=()):
    con = sqlite3.connect(DB)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def require_user(user_id):
    if not q("SELECT 1 FROM users WHERE user_id = ?", (user_id,)):
        raise HTTPException(404, f"unknown user {user_id}")


class Ask(BaseModel):
    user_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None  # Optional[] (not `str | None`): pydantic evaluates it on Python 3.9


@app.get("/api/health")
def health():
    wells = q("SELECT COUNT(*) AS n FROM wells")[0]["n"]
    try:
        b = copilot().backend
        ready, detail = b.ready()
        info = {"provider": b.provider, "model": b.model}
    except Exception as err:  # e.g. unknown provider in the environment
        ready, detail, info = False, str(err), {}
    return {"ok": True, "wells": wells, "model_ready": ready, "model_detail": detail, **info}


@app.get("/api/users")
def users():
    return q("SELECT u.user_id, u.name, u.role, u.team_id, t.name AS team FROM users u LEFT JOIN teams t USING(team_id) ORDER BY u.user_id")


@app.get("/api/conversations")
def conversations(user_id: str):
    require_user(user_id)
    return q("SELECT conversation_id, started_at, title FROM conversations WHERE user_id = ? ORDER BY started_at DESC", (user_id,))


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, user_id: str):
    require_user(user_id)
    owner = q("SELECT user_id FROM conversations WHERE conversation_id = ?", (conversation_id,))
    if not owner or owner[0]["user_id"] != user_id:
        raise HTTPException(404, "no such conversation for this user")
    turns = q("SELECT role, text, created_at, answer_json FROM turns WHERE conversation_id = ? AND user_id = ? ORDER BY seq",
              (conversation_id, user_id))
    for t in turns:
        t["answer"] = json.loads(t.pop("answer_json")) if t.get("answer_json") else None
    return {"conversation_id": conversation_id, "turns": turns}


@app.get("/api/feedback")
def feedback(limit: int = 50):
    """Shared knowledge users have added (corrections, rules, notes, claims), newest first."""
    return q("SELECT 'OVL-' || o.seq AS entry_id, o.kind, o.target_type, o.target_ref, o.field, o.original_value, o.new_value, "
             "o.text, o.reason, o.status, o.created_at, u.name AS author FROM overlay_entries o LEFT JOIN users u ON u.user_id = o.author_user_id "
             "ORDER BY o.seq DESC LIMIT ?", (limit,))


@app.post("/api/ask")
def ask(body: Ask):
    require_user(body.user_id)
    if body.conversation_id:
        owner = q("SELECT user_id FROM conversations WHERE conversation_id = ?", (body.conversation_id,))
        if owner and owner[0]["user_id"] != body.user_id:
            raise HTTPException(404, "no such conversation for this user")
    ready, detail = copilot().backend.ready()
    if not ready:
        raise HTTPException(503, f"The model is not available: {detail}.")
    try:
        return copilot().ask(body.user_id, body.question, body.conversation_id)
    except Exception as err:  # surface model/provider problems to the UI instead of a bare 500
        raise HTTPException(502, f"{type(err).__name__}: {err}")


if os.path.isdir(WEB):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(os.path.join(WEB, "index.html"))
