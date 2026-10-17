"""The copilot: a tool-use loop over a language model. The model plans and writes; tools supply every fact.
The model provider is pluggable (see llm.py): local Ollama for free, or Claude, OpenRouter, Google AI Studio.

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

from . import fieldtools, llm, tools
from .overlay import OverlayError

MAX_STEPS = 12
COMPACT_ROWS = 40  # rows a small model sees per tool result; totals stay in the summary
# Words in the question that name a visual, used when the model answers without choosing one.
VISUAL_WORDS = [("heatmap", "heatmap"), ("heat map", "heatmap"), ("status card", "card"), ("card", "card"),
                ("line chart", "line"), ("over time", "line"), ("bar chart", "bar"), ("bar", "bar"),
                ("map", "map"), ("table", "table")]

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
- "Right now" means each well's current state today: its latest inspection, unless a newer shut-in or restart
  operator note overrides it (SOP-OPS OPS-3; well_status and status_conflicts apply this). Plugged and abandoned
  wells are closed for good. Periods like "last 4 months" end today. A repair belongs to a period by its start
  date. Today is {today}.
- If a question needs a measurement the tools do not provide (pressure, oil volumes, flow rates), call
  data_coverage and say plainly that the data does not contain it; offer what the data does show and label any
  proxy as a proxy. Never present a proxy (e.g. temperature, producing days) as the thing that was asked for.
- Use the dedicated tools: gas_reports (gas odor responses, H2S readings), bypasses (temporary bypasses and MOC
  approvals), status_conflicts (inspection vs operator notes), operator_concerns (wells that sound or behave
  wrong), recalled_pumps (supplier recall), letter_compliance (regulator letters), freeze_checks (cold weather),
  shut_in_delays (what repairs wait on), temperature_by_area (which areas run hotter), team_info (team lead and
  wells), production_proxy (no volumes: proxy only), search_docs/read_doc_section for what an SOP says.
- Say where every fact came from: a record ID, a document section, or a person (OVL- entry with their name).
- When something is not producing and no record explains it, say so plainly and suggest a check. Do not invent causes.
- Explain the numbers: when a tool reports records it excluded (e.g. detections corrected as not oil) or the cause
  of a period (possible_causes, notes), say so with their IDs.
- Cite record and document IDs only; never cite internal result IDs such as R1.
- Never say a rule was met (or a deadline kept) unless a tool returned that check. If the data for a check is
  missing, say it is unknown.
- Honesty about conflicts: if records, notes or people disagree (including the user), show both sides and which one
  the data supports. Never silently pick one. Unconfirmed claims never override inspection data.
- When the user corrects data or the AI's analysis, record it with record_correction (one inspection) or
  record_type_rule (a whole well type). Then state plainly what changes, using the returned consequences
  (e.g. "WELL-0164 is now producing", "the spill is now reportable; the written notice is overdue"), and
  mention any record that disagrees.
  When the user states a fact about a well ("shut in from 17 Oct", "running fine"), record it with
  record_well_statement, passing claimed_status when the statement says the well is or is not producing.
  When the user says a recalled pump was swapped but no work order exists, use report_equipment_swap; if they
  are passing on what someone else said ("Nathan told me..."), pass relayed_from with that person's name. A
  user report is not a work order: never state that a pump IS replaced on someone's word; say who reported it,
  when, and that the records still show the recalled pump. If the person named never said it, say so.
- When a user states a personal display preference ("just for me", "sort by...", "show costs in thousands"),
  save it with set_my_preference. It applies only to that user; tables are then sorted and converted for them.
- Questions about "what we discussed before" use recall_my_conversations, which only ever returns the current
  user's own history. If it has nothing, or nothing that matches what the user describes (e.g. they say "three
  wells" but their history has one), say there is no such earlier conversation with this user; never substitute.
- When a value was corrected, say who corrected it and why.
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
    def __init__(self, db_path, backend=None, client=None):
        """client: an Anthropic-style client (used by tests to script the model)."""
        self.db_path = db_path
        self.backend = backend or llm.make_backend(client)
        self.model = self.backend.model
        self._states = {}  # conversation_id -> backend message state (append-only within a process)

    def ask(self, user_id, question, conversation_id=None):
        con = sqlite3.connect(self.db_path)
        try:
            if not con.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
                raise ValueError(f"unknown user {user_id}")
            conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:12]}"
            ctx = tools.Context(con, user_id, conversation_id=conversation_id)
            user_name, role, team = con.execute("SELECT u.name, u.role, IFNULL(t.name, u.team_id) FROM users u "
                                                "LEFT JOIN teams t ON t.team_id = u.team_id WHERE u.user_id = ?", (user_id,)).fetchone()
            prefs = fieldtools.user_prefs(con, user_id)
            mine = ", ".join(a for (a,) in con.execute("SELECT DISTINCT a.name FROM wells w JOIN areas a USING (area_id) JOIN users u "
                                                       "ON u.team_id = w.team_id WHERE u.user_id = ? ORDER BY 1", (user_id,)))
            who = f"[{user_name}, {user_id}] [{(role or '').replace('_', ' ')}, {team}" + (f"; their area: {mine}]" if mine else "]")
            prompt = who + (f" [their saved preferences: {prefs}]" if prefs else "") + f" {question}"
            state = self._states.get(conversation_id)
            if state is None:
                system = INSTRUCTIONS.format(today=ctx.today.isoformat(), directory=directory(con))
                state = self.backend.start(system, self._history(con, conversation_id, user_id), prompt)
                self._states[conversation_id] = state
            else:
                state["messages"].append({"role": "user", "content": prompt})
            answer = self._loop(ctx, state, question)
            prefs = fieldtools.user_prefs(con, user_id)  # re-read: this turn may have saved one
            if prefs and answer.get("visual"):
                answer["visual"] = fieldtools.apply_prefs(answer["visual"], prefs)
            self._save_turns(con, conversation_id, user_id, question, answer)
            answer["conversation_id"] = conversation_id
            return answer
        finally:
            con.close()

    def _loop(self, ctx, state, question):
        results, final = {}, None
        usage = {"tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "model_calls": 0}
        defs = tools.tool_definitions() + [PRESENT]
        for _ in range(MAX_STEPS):
            step = self.backend.step(state, defs)
            usage["model_calls"] += 1
            for k, v in step.usage.items():
                usage[k] = usage.get(k, 0) + v
            if step.refused:
                final = {"answer": "I can't help with that request.", "visual_type": "none"}
                break
            if not step.calls:
                final = {"answer": step.text or "(no answer)", "visual_type": "auto"}
                break
            replies = []
            for call in step.calls:
                if call.name == "present_answer":
                    final = dict(call.input)
                    replies.append((call.id, "delivered", False))
                    continue
                usage["tool_calls"] += 1
                if call.error:
                    replies.append((call.id, f"Error: {call.error}", True))
                    continue
                rid = f"R{len(results) + 1}"
                try:
                    out = tools.run(ctx, call.name, call.input)
                    results[rid] = {"tool": call.name, "input": call.input, **out}
                    replies.append((call.id, json.dumps(self._for_model(rid, out), default=str), False))
                except (tools.ToolError, OverlayError, TypeError) as err:
                    replies.append((call.id, f"Error: {err}", True))
            self.backend.add_results(state, replies)
            if final:
                break
        else:
            final = {"answer": "I could not finish this answer within the step limit.", "visual_type": "none"}
        if not final.get("answer") and results:
            final["answer"] = "(the model returned no text; the records it used are below)"
        return self._compose(final, results, usage, question)

    def _for_model(self, rid, out):
        """What the model sees: data, flags and evidence refs; visuals only as the types available.
        Small models get a compact view (fewer rows, no empty fields) so the context does not overflow."""
        rows, evidence = out["rows"], out["evidence"][:40]
        if self.backend.compact:
            rows = [{k: v for k, v in r.items() if v not in (None, "", [], {})} for r in rows[:COMPACT_ROWS]]
            evidence = [{"kind": e["kind"], "ref": e["ref"]} for e in evidence[:15]]
        view = {"result_id": rid, "summary": out["summary"], "rows": rows, "flags": out["flags"],
                "evidence": evidence, "visual_types_available": sorted(out["visuals"])}
        if len(out["rows"]) > len(rows):
            view["note"] = f"showing {len(rows)} of {len(out['rows'])} rows; use the summary for totals"
        return view

    def _compose(self, final, results, usage, question=""):
        vtype, rid = final.get("visual_type"), final.get("visual_result_id")
        if vtype in (None, "auto"):
            vtype = next((v for word, v in VISUAL_WORDS if word in question.lower()), "auto")
        visual = None
        if vtype == "auto":
            visual = self._auto_visual(results)
        elif vtype != "none":
            source = results.get(rid) if rid in results and vtype in results[rid]["visuals"] else None
            source = source or next((r for r in reversed(list(results.values())) if vtype in r["visuals"]), None)
            if source:
                visual = source["visuals"].get(vtype) or source["visuals"].get("table")
                if visual and final.get("visual_title"):
                    visual = dict(visual, title=final["visual_title"])
        evidence, seen, flags = [], set(), []
        for r in results.values():
            for e in r["evidence"]:
                key = (e["kind"], e["ref"])
                if key not in seen:
                    seen.add(key)
                    evidence.append(e)
            flags += r["flags"]
        usage["cost_usd"] = llm.cost(self.model, usage)
        usage["model"] = self.model
        usage["provider"] = self.backend.provider
        # Result IDs (R1, R2...) are internal handles for choosing visuals, not citations; drop any that leak into prose.
        text = re.sub(r"\s*\((?:R\d+(?:,\s*)?)+\)", "", final.get("answer", ""))
        # Tool names are not sources either: "(data_coverage)" -> "", "the production_proxy tool" stays readable.
        names = "|".join(map(re.escape, tools.TOOLS))
        text = re.sub(rf"\s*\((?:{names})(?:,\s*(?:{names}))*\)", "", text)
        return {"text": text, "visual": visual, "evidence": evidence, "flags": flags,
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

    # --- private memory -------------------------------------------------------------------------

    @staticmethod
    def _history(con, conversation_id, user_id):
        """Earlier turns of this conversation as plain text (used when the process has no live state for it)."""
        return [{"role": role, "content": text} for role, text in con.execute(
            "SELECT role, text FROM turns WHERE conversation_id = ? AND user_id = ? ORDER BY seq", (conversation_id, user_id))]

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
