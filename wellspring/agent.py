"""The copilot: a tool-use loop over Claude. The model plans and writes; tools supply every fact.

One call to `Copilot.ask(user_id, question, conversation_id)` returns an Answer dict:
  text      markdown answer
  visual    a visual spec taken from a tool result (never numbers the model typed itself)
  evidence  records and documents behind the answer
  flags     stale records, conflicts, corrections applied, unconfirmed claims...
  usage     tool calls, tokens and cost in USD
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

import anthropic

from . import tools
from .overlay import OverlayError

MODEL = os.environ.get("WELLSPRING_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("WELLSPRING_EFFORT", "medium")
MAX_STEPS = 12
# USD per million tokens: input, output, cache write (5 min), cache read.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
          "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01)}

PRESENT = {
    "name": "present_answer",
    "description": "Give the final answer to the user. Call this exactly once, last, after the data tools.",
    "input_schema": {
        "type": "object", "additionalProperties": False, "required": ["answer"],
        "properties": {
            "answer": {"type": "string", "description": "Markdown answer. Cite record/document IDs inline, e.g. (INS-0001-0203, OM-T200 section 9.3)."},
            "visual_type": {"type": "string", "enum": ["card", "table", "line", "bar", "map", "heatmap", "none"]},
            "visual_result_id": {"type": "string", "description": "result_id (e.g. R2) of the tool result whose data the visual shows"},
            "visual_title": {"type": "string"},
        },
    },
}

INSTRUCTIONS = """You are Wellspring, an asset intelligence copilot for an oil field's operators. Answer their plain-English
questions about wells correctly and with evidence.

How to work:
- Every number, date, ID and status must come from a tool result. Never compute downtime, totals or thresholds
  yourself and never guess; if the tools cannot answer, say so and say what is missing.
- Rules (production thresholds, spill classes, deadlines) live in the manuals and SOPs; the tools apply them and
  return the source. Cite record IDs (inspections, repairs, reports, regulator events, notes, OVL- entries) and
  document sections inline.
- "Right now" means each well's latest inspection. Periods like "last 4 months" end today. A repair belongs to a
  period by its start date. Today is {today}.
- When something is not producing and no record explains it, say so plainly and suggest a check. Do not invent causes.
- Honesty about conflicts: if records, notes or people disagree (including the user), show both sides and which one
  the data supports. Never silently pick one. Unconfirmed claims never override inspection data.
- When the user corrects data or the AI's analysis, record it with record_correction (one inspection) or
  record_type_rule (a whole well type), then re-check what changes and mention any record that disagrees.
  When the user states a fact about a well ("shut in from 17 Oct", "running fine"), record it with
  record_well_statement, passing claimed_status when the statement says the well is or is not producing.
- Questions about "what we discussed before" use recall_my_conversations, which only ever returns the current
  user's own history. If it has nothing, say there is no such earlier conversation with this user.
- Finish by calling present_answer once. Choose the visual: the one the user names, otherwise card for one well
  right now, table for a list, line for change over time, bar for comparing a few wells or areas, map for
  locations, heatmap for two dimensions such as area x month, or none for a short fact. The visual must come from
  a tool result (visual_result_id) that offers that type.
- Be concise: lead with the direct answer, then the evidence.

Fleet directory:
{directory}
"""


def directory(con):
    q = lambda sql: con.execute(sql).fetchall()
    lines = ["Areas: " + "; ".join(f"{a} = {n} ({c} wells, team {t})" for a, n, t, c in q(
        "SELECT a.area_id, a.name, a.team_id, COUNT(w.asset_id) FROM areas a LEFT JOIN wells w USING(area_id) GROUP BY a.area_id"))]
    lines.append("Teams: " + "; ".join(f"{i} = {n}" for i, n in q("SELECT team_id, name FROM teams")))
    lines.append("Users: " + "; ".join(f"{i} {n} ({r}, {t})" for i, n, r, t in q("SELECT user_id, name, role, team_id FROM users")))
    lines.append("Well types: " + "; ".join(f"{t}: {c} wells" for t, c in q("SELECT well_type, COUNT(*) FROM wells GROUP BY well_type")))
    lines.append("Equipment kinds: " + ", ".join(k for (k,) in q("SELECT DISTINCT kind FROM equipment ORDER BY 1")))
    return "\n".join(lines)


class Copilot:
    def __init__(self, db_path, model=MODEL, effort=EFFORT, client=None):
        self.db_path = db_path
        self.model, self.effort = model, effort
        self.client = client or anthropic.Anthropic()
        self._histories = {}  # conversation_id -> full message list (kept append-only within a process)

    def _con(self):
        con = sqlite3.connect(self.db_path)
        return con

    def ask(self, user_id, question, conversation_id=None):
        con = self._con()
        try:
            if not con.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
                raise ValueError(f"unknown user {user_id}")
            conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:12]}"
            ctx = tools.Context(con, user_id, conversation_id=conversation_id)
            user_name = con.execute("SELECT name FROM users WHERE user_id = ?", (user_id,)).fetchone()[0]
            system = [{"type": "text", "text": INSTRUCTIONS.format(today=ctx.today.isoformat(), directory=directory(con))}]
            messages = self._history(con, conversation_id, user_id)
            messages.append({"role": "user", "content": f"[{user_name}, {user_id}] {question}"})
            answer = self._loop(ctx, system, messages)
            self._save_turns(con, conversation_id, user_id, question, answer)
            answer["conversation_id"] = conversation_id
            return answer
        finally:
            con.close()

    def _loop(self, ctx, system, messages):
        results, usage = {}, {"tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "model_calls": 0}
        final = None
        for _ in range(MAX_STEPS):
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=system, messages=messages,
                tools=tools.tool_definitions() + [PRESENT], output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            self._count(usage, response)
            if response.stop_reason == "refusal":
                final = {"answer": "I can't help with that request.", "visual_type": "none"}
                break
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                text = "".join(b.text for b in response.content if b.type == "text")
                final = {"answer": text or "(no answer)", "visual_type": "auto"}
                break
            tool_results = []
            for call in calls:
                if call.name == "present_answer":
                    final = dict(call.input)
                    tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": "delivered"})
                    continue
                usage["tool_calls"] += 1
                rid = f"R{len(results) + 1}"
                try:
                    out = tools.run(ctx, call.name, dict(call.input))
                    results[rid] = {"tool": call.name, "input": dict(call.input), **out}
                    tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(self._for_model(rid, out), default=str)})
                except (tools.ToolError, OverlayError, TypeError) as err:
                    tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Error: {err}", "is_error": True})
            messages.append({"role": "user", "content": tool_results})
            if final:
                break
        else:
            final = {"answer": "I could not finish this answer within the step limit.", "visual_type": "none"}
        return self._compose(final, results, usage)

    @staticmethod
    def _for_model(rid, out):
        """What the model sees: data and flags in full, evidence capped, visuals only as available types."""
        return {"result_id": rid, "summary": out["summary"], "rows": out["rows"], "flags": out["flags"],
                "evidence": out["evidence"][:40], "visual_types_available": sorted(out["visuals"])}

    def _compose(self, final, results, usage):
        visual = None
        vtype, rid = final.get("visual_type"), final.get("visual_result_id")
        if vtype == "auto":
            visual = self._auto_visual(results)
        elif vtype and vtype != "none":
            source = results.get(rid) or next((r for r in reversed(list(results.values())) if vtype in r["visuals"]), None)
            if source:
                visual = source["visuals"].get(vtype) or source["visuals"].get("table")
                if visual:
                    visual = dict(visual, title=final.get("visual_title") or visual.get("title"))
        evidence, seen, flags = [], set(), []
        for r in results.values():
            for e in r["evidence"]:
                key = (e["kind"], e["ref"])
                if key not in seen:
                    seen.add(key)
                    evidence.append(e)
            flags += r["flags"]
        p = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        usage["cost_usd"] = round((usage["input_tokens"] * p[0] + usage["output_tokens"] * p[1]
                                   + usage["cache_write_tokens"] * p[2] + usage["cache_read_tokens"] * p[3]) / 1e6, 4)
        usage["model"] = self.model
        return {"text": final.get("answer", ""), "visual": visual, "evidence": evidence, "flags": flags,
                "tools_used": [{"id": k, "tool": v["tool"], "input": v["input"]} for k, v in results.items()], "usage": usage}

    @staticmethod
    def _auto_visual(results):
        if not results:
            return None
        last = list(results.values())[-1]["visuals"]
        for t in ("card", "heatmap", "line", "bar", "table", "map"):
            if t in last:
                return last[t]
        return None

    @staticmethod
    def _count(usage, r):
        u = r.usage
        usage["model_calls"] += 1
        usage["input_tokens"] += u.input_tokens or 0
        usage["output_tokens"] += u.output_tokens or 0
        usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0

    # --- private memory -------------------------------------------------------------------------

    def _history(self, con, conversation_id, user_id):
        if conversation_id in self._histories:
            return self._histories[conversation_id]
        msgs = []
        for role, text in con.execute("SELECT role, text FROM turns WHERE conversation_id = ? AND user_id = ? ORDER BY seq",
                                      (conversation_id, user_id)):
            msgs.append({"role": role, "content": text})
        self._histories[conversation_id] = msgs
        return msgs

    @staticmethod
    def _save_turns(con, conversation_id, user_id, question, answer):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute("INSERT OR IGNORE INTO conversations (conversation_id, user_id, started_at, title) VALUES (?, ?, ?, ?)",
                    (conversation_id, user_id, now, question[:80]))
        wells = sorted(set(re.findall(r"WELL-\d{4}", answer["text"])))
        con.execute("INSERT INTO turns (conversation_id, user_id, role, text, asset_ids, created_at) VALUES (?, ?, 'user', ?, ?, ?)",
                    (conversation_id, user_id, question, json.dumps(sorted(set(re.findall(r"WELL-\d{4}", question)))), now))
        con.execute("INSERT INTO turns (conversation_id, user_id, role, text, asset_ids, created_at, answer_json) VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
                    (conversation_id, user_id, answer["text"], json.dumps(wells), now, json.dumps(answer, default=str)))
        con.commit()
