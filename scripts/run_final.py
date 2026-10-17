"""Run the organizers' final questions (tests/e2e/final_cases.py) through the copilot, in order, on a fresh copy of
the test dataset, and save every turn's full answer (text, visual, evidence, flags, tools, usage) for the write-up.

    .venv/bin/python scripts/run_final.py [--data data/test-data/dataset] [--db var/final.db] [--only F3,F8]

Writes var/final/<scenario>.json per scenario and var/final/transcript.md; prints a pass/fail line per turn using
the e2e harness checks (visual type, citations, must / must_not).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.e2e.final_cases import FINAL  # noqa: E402
from tests.e2e.harness import score, to_reply  # noqa: E402
from wellspring import loader  # noqa: E402
from wellspring.agent import Copilot  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/test-data/dataset")
    ap.add_argument("--db", default="var/final.db")
    ap.add_argument("--out", default="var/final")
    ap.add_argument("--only", help="comma-separated scenario IDs, e.g. F3,F8 (memory still carries over within the run)")
    args = ap.parse_args()
    if os.path.exists(args.db):
        os.remove(args.db)
    loader.load([args.data], args.db, fresh=True)
    os.makedirs(args.out, exist_ok=True)
    import sqlite3
    con = sqlite3.connect(args.db)
    ids = {n: u for u, n in con.execute("SELECT user_id, name FROM users")}
    con.close()
    bot = Copilot(args.db)
    only = set(args.only.split(",")) if args.only else None
    md = [f"# Final questions: answers\n\nModel: {bot.backend.provider} {bot.backend.model}\n"]
    conv = {}
    for sc in FINAL:
        if only and sc.id not in only:
            continue
        out, md_sc = [], [f"\n## {sc.id}. {sc.title}\n"]
        for i, t in enumerate(sc.turns, 1):
            uid = ids[t.user]
            cid = None if t.new_chat or i == 1 else conv.get(uid)
            started = time.time()
            try:
                a = bot.ask(uid, t.message, cid)
                err = None
            except Exception as e:  # keep going; record the failure
                a, err = {"text": "", "visual": None, "evidence": [], "flags": [], "tools_used": [], "usage": {}}, f"{type(e).__name__}: {e}"
            conv[uid] = a.get("conversation_id", cid)
            secs = round(time.time() - started, 1)
            fails = score(t, to_reply(a)) if not err else [err]
            print(f"{sc.id}.{i} {t.user}: {'ok' if not fails else 'FAIL ' + '; '.join(fails)} ({secs}s, tools: {[x['tool'] for x in a.get('tools_used', [])]})", flush=True)
            out.append({"turn": i, "user": t.user, "message": t.message, "answer": a, "seconds": secs, "checks_failed": fails, "error": err})
            v = a.get("visual") or {}
            md_sc.append(f"\n**{t.user}:** {t.message}\n\n{a.get('text', '')}\n\n_Visual: {v.get('type', 'none')}"
                         + (f" ({v.get('title')})" if v.get("title") else "") + f" · tools: {', '.join(x['tool'] for x in a.get('tools_used', []))}"
                         + f" · {secs}s_\n")
            if v.get("type") == "table":
                md_sc.append("\n| " + " | ".join(map(str, v["columns"])) + " |\n|" + "---|" * len(v["columns"]) + "\n"
                             + "\n".join("| " + " | ".join("" if c is None else str(c).replace("|", "/") for c in r) + " |" for r in v["rows"][:30]) + "\n")
            flags = a.get("flags") or []
            if flags:
                md_sc.append("\nFlags: " + "; ".join(f"[{f['kind']}] {f['message']}" for f in flags[:10]) + "\n")
        with open(os.path.join(args.out, f"{sc.id}.json"), "w") as f:
            json.dump(out, f, indent=2, default=str)
        md += md_sc
        with open(os.path.join(args.out, "transcript.md"), "w") as f:
            f.write("".join(md))


if __name__ == "__main__":
    main()
