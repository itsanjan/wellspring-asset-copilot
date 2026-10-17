"""Ask the copilot from the command line.

    .venv/bin/python -m wellspring.chat --user U-201 "Is WELL-0001 producing oil right now?"
    .venv/bin/python -m wellspring.chat --user U-201 --conversation conv-abc "and why?"

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile). WELLSPRING_MODEL / WELLSPRING_EFFORT override the model.
"""
from __future__ import annotations

import argparse
import json
import sys

from .agent import Copilot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--user", required=True, help="user ID from fleet/users.json, e.g. U-201")
    ap.add_argument("--conversation", help="continue an earlier conversation")
    ap.add_argument("--db", default="var/wellspring.db")
    ap.add_argument("--json", action="store_true", help="print the full answer object")
    args = ap.parse_args(argv)
    a = Copilot(args.db).ask(args.user, args.question, args.conversation)
    if args.json:
        print(json.dumps(a, indent=2, default=str))
        return 0
    print(a["text"], "\n")
    if a["visual"]:
        print(f"[visual: {a['visual']['type']}] {a['visual'].get('title') or ''}")
    for f in a["flags"]:
        print(f"[{f['kind']}] {f['message']}")
    print(f"[evidence: {len(a['evidence'])} items] [tools: {', '.join(t['tool'] for t in a['tools_used'])}]")
    u = a["usage"]
    print(f"[{u['model']}: {u['model_calls']} calls, {u['input_tokens']}+{u['cache_read_tokens']} cached in, {u['output_tokens']} out, ${u['cost_usd']}]")
    print(f"[conversation: {a['conversation_id']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
