"""Run the test suite and write a static HTML report (for GitHub Pages) plus results.json.

    python3 scripts/test_report.py --out site    # runs `unittest discover -s tests -t .`, then writes the report

Writes site/index.html (test results), site/results.json, and site/qa-plan.html (docs/QA-VALIDATION-PLAN.md,
rendered when the `markdown` package is installed, otherwise shown as plain text). Exits 1 if any test fails,
errors, or unexpectedly passes; skips and expected failures are reported but do not fail the run.
Standard library only for the results.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import sys
import textwrap
import time
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# What each test module proves, in the order the QA plan lists the layers.
LAYERS = [
    ("tests.test_rules", "L1 Unit", "Rule parsing from the manuals"),
    ("tests.test_ground_truth", "L2 Smoke", "Ground-truth script still reproduces Q1-Q8"),
    ("tests.test_parity", "L3 Parity", "Product SQL views agree with the LLM-free ground truth on all 200 wells"),
    ("tests.test_tools", "L3b Tools", "Tool layer gives the organizers' expected facts; feedback, scoped rules, claims, private memory"),
    ("tests.test_agent", "L3c Agent", "Agent loop with a scripted model: visuals, evidence, cost, errors, history"),
    ("tests.test_llm", "L3c Backends", "Provider selection, cost table, OpenAI-compatible backend for local models"),
    ("tests.test_server", "L3d HTTP API", "Validation, error mapping, shared feedback, conversation privacy"),
    ("tests.test_generalization", "L6 Generalization", "Changed manual, new well type, second dataset (synthetic)"),
    ("tests.e2e.test_harness", "L4 E2E scorer", "The answer scorer is not vacuous"),
    ("tests.e2e.test_pipeline", "L4b E2E pipeline", "Real Q1 case through adapter, agent, tools and database (scripted model)"),
    ("tests.e2e.test_copilot", "L5 Live model", "Q1-Q18 through a real model (manual; skipped in CI)"),
]
LAYER_BY_MODULE = {m: (name, desc) for m, name, desc in LAYERS}

PASS, FAIL, ERROR, SKIP, XFAIL, XPASS = "pass", "fail", "error", "skip", "xfail", "xpass"
LABEL = {PASS: "Passed", FAIL: "Failed", ERROR: "Error", SKIP: "Skipped", XFAIL: "Expected failure", XPASS: "Unexpected pass"}
BAD = {FAIL, ERROR, XPASS}


class Recorder(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records = []
        self._t0 = {}

    def startTest(self, test):
        self._t0[id(test)] = time.perf_counter()
        super().startTest(test)

    def _add(self, test, status, detail=""):
        t0 = self._t0.pop(id(test), None)
        self.records.append({"id": test.id(), "status": status, "detail": detail.strip(),
                             "seconds": round(time.perf_counter() - t0, 3) if t0 else 0.0})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, PASS)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, FAIL, self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, ERROR, self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, SKIP, reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._add(test, XFAIL, (test._testMethodDoc or "").strip() or "known issue, see the QA plan")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._add(test, XPASS, "marked as an expected failure but passed; remove the marker")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._add(subtest, FAIL, self._exc_info_to_string(err, test))


def module_of(test_id):
    m = re.match(r"setUpModule \((.+)\)|tearDownModule \((.+)\)", test_id)
    if m:
        return m.group(1) or m.group(2)
    parts = test_id.split(".")
    return ".".join(parts[:-2]) if len(parts) > 2 else test_id


def pretty(test_id):
    name = test_id.split(".")[-1]
    if name.startswith("test_"):
        name = name[5:]
    return name.replace("_", " ")


def run_suite():
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    runner = unittest.TextTestRunner(resultclass=Recorder, verbosity=2, stream=sys.stdout)
    t0 = time.perf_counter()
    result = runner.run(suite)
    return result.records, round(time.perf_counter() - t0, 2)


def context():
    env = os.environ
    sha = env.get("GITHUB_SHA", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = env.get("GITHUB_RUN_ID", "")
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "python": platform.python_version(),
        "sha": sha[:7] if sha else "local",
        "commit_url": f"{server}/{repo}/commit/{sha}" if sha and repo else "",
        "branch": env.get("GITHUB_REF_NAME", "local"),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if run_id and repo else "",
        "repo_url": f"{server}/{repo}" if repo else "",
        "repo": repo,
    }


def group(records):
    by_mod = {}
    for r in records:
        by_mod.setdefault(module_of(r["id"]), []).append(r)
    order = [m for m, _, _ in LAYERS if m in by_mod] + sorted(m for m in by_mod if m not in LAYER_BY_MODULE)
    return [(m, by_mod[m]) for m in order]


def counts(records):
    c = {s: 0 for s in LABEL}
    for r in records:
        c[r["status"]] += 1
    return c


CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--surface-2:#f2f1ed;--ink:#0b0b0b;--ink-2:#52514e;--muted:#6e6c66;--border:rgba(11,11,11,.12);
--good:#006300;--good-bg:#e3f3e3;--bad:#a02323;--bad-bg:#fbe5e5;--warn:#7a5200;--warn-bg:#fdf0d0;--info:#184f95;--info-bg:#e3eefb;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0d0d;--surface:#1a1a19;--surface-2:#232321;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#a09e96;--border:rgba(255,255,255,.14);
--good:#6fd36f;--good-bg:#12301a;--bad:#ff8a8a;--bad-bg:#3a1515;--warn:#f3c969;--warn-bg:#352a0e;--info:#8fbdf5;--info-bg:#14243a;--accent:#3987e5}}
:root[data-theme="dark"]{--page:#0d0d0d;--surface:#1a1a19;--surface-2:#232321;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#a09e96;--border:rgba(255,255,255,.14);
--good:#6fd36f;--good-bg:#12301a;--bad:#ff8a8a;--bad-bg:#3a1515;--warn:#f3c969;--warn-bg:#352a0e;--info:#8fbdf5;--info-bg:#14243a;--accent:#3987e5}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.meta{color:var(--ink-2);font-size:13px;margin:0 0 20px}.meta a,nav a{color:var(--accent)}
nav{display:flex;gap:16px;margin:0 0 16px;font-size:14px}
.verdict{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:650;font-size:14px;margin-bottom:12px}
.ok{background:var(--good-bg);color:var(--good)}.no{background:var(--bad-bg);color:var(--bad)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:24px;line-height:1.2}.card span{color:var(--ink-2);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:top}th{background:var(--surface-2);font-weight:600;font-size:13px}
tr:last-child td{border-bottom:0}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
details{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin:10px 0}
summary{cursor:pointer;padding:10px 12px;font-weight:600;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}
summary small{font-weight:400;color:var(--ink-2)}
.tests{padding:0 12px 10px}.t{display:grid;grid-template-columns:150px 1fr 60px;gap:8px;padding:5px 0;border-top:1px solid var(--border);font-size:14px}
.t>.badge{align-self:start;justify-self:start}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.b-pass{background:var(--good-bg);color:var(--good)}.b-fail,.b-error,.b-xpass{background:var(--bad-bg);color:var(--bad)}
.b-skip{background:var(--surface-2);color:var(--ink-2)}.b-xfail{background:var(--warn-bg);color:var(--warn)}
pre{margin:4px 0 0;white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,monospace;background:var(--surface-2);padding:8px;border-radius:6px;max-height:260px;overflow:auto}
.note{color:var(--ink-2);font-size:13px}
@media (max-width:560px){.t{grid-template-columns:1fr 50px}.t .badge{grid-column:1/-1;justify-self:start}}
"""


def render_index(records, seconds, ctx):
    c = counts(records)
    bad = sum(c[s] for s in BAD)
    verdict = ('<span class="verdict ok">All tests passed</span>' if not bad
               else f'<span class="verdict no">{bad} test{"s" if bad != 1 else ""} need attention</span>')
    cards = "".join(f'<div class="card"><b>{n}</b><span>{html.escape(label)}</span></div>'
                    for n, label in [(len(records), "Tests run"), (c[PASS], "Passed"), (bad, "Failed or errored"),
                                     (c[SKIP], "Skipped"), (c[XFAIL], "Known issues"), (f"{seconds}s", "Duration")])
    groups = group(records)
    rows = []
    for mod, recs in groups:
        mc = counts(recs)
        layer, desc = LAYER_BY_MODULE.get(mod, (mod.replace("tests.", ""), ""))
        rows.append(f"<tr><td>{html.escape(layer)}</td><td>{html.escape(desc)}</td><td class='n'>{len(recs)}</td>"
                    f"<td class='n'>{mc[PASS]}</td><td class='n'>{mc[SKIP]}</td><td class='n'>{mc[XFAIL]}</td>"
                    f"<td class='n'>{mc[FAIL] + mc[ERROR] + mc[XPASS]}</td></tr>")
    layers = ("<table><thead><tr><th>Layer</th><th>What it proves</th><th class='n'>Tests</th><th class='n'>Pass</th>"
              "<th class='n'>Skip</th><th class='n'>Known</th><th class='n'>Fail</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    details = []
    for mod, recs in groups:
        mc = counts(recs)
        layer, _ = LAYER_BY_MODULE.get(mod, (mod, ""))
        open_attr = " open" if any(r["status"] in BAD for r in recs) else ""
        lines = []
        for r in sorted(recs, key=lambda r: (r["status"] in (PASS,), r["id"])):
            extra = f"<pre>{html.escape(r['detail'])}</pre>" if r["detail"] and r["status"] != PASS else ""
            lines.append(f"<div class='t'><span class='badge b-{r['status']}'>{LABEL[r['status']]}</span>"
                         f"<span>{html.escape(pretty(r['id']))}<br><span class='note'>{html.escape(r['id'].split('.')[-1])}</span>{extra}</span>"
                         f"<span class='note' style='text-align:right'>{r['seconds']}s</span></div>")
        details.append(f"<details{open_attr}><summary>{html.escape(layer)} <small>{html.escape(mod)} · {len(recs)} tests · "
                       f"{mc[PASS]} passed, {mc[SKIP]} skipped, {mc[XFAIL]} known issues</small></summary>"
                       f"<div class='tests'>{''.join(lines)}</div></details>")

    known = [r for r in records if r["status"] == XFAIL]
    known_html = ""
    if known:
        items = "".join(f"<li><b>{html.escape(pretty(r['id']))}</b>: {html.escape(textwrap.shorten(' '.join(r['detail'].split()), 320, placeholder=' …'))}</li>" for r in known)
        known_html = ("<h2>Known issues pinned by tests</h2><p class='note'>These tests are marked as expected failures: the behaviour is "
                      "wrong today, the test describes the right behaviour, and the suite fails if one starts passing without the marker "
                      f"being removed.</p><ul>{items}</ul>")

    links = [f'<a href="qa-plan.html">QA validation plan</a>', '<a href="results.json">results.json</a>']
    if ctx["repo_url"]:
        links.append(f'<a href="{ctx["repo_url"]}">Repository</a>')
    if ctx["run_url"]:
        links.append(f'<a href="{ctx["run_url"]}">CI run</a>')
    commit = (f'<a href="{ctx["commit_url"]}">{ctx["sha"]}</a>' if ctx["commit_url"] else html.escape(ctx["sha"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wellspring Test Report</title><style>{CSS}</style></head><body><main>
<nav>{' '.join(links)}</nav>
<h1>Wellspring test report</h1>
<p class="meta">Commit {commit} on {html.escape(ctx['branch'])} · Python {html.escape(ctx['python'])} · generated {html.escape(ctx['generated_at'])}</p>
{verdict}
<div class="cards">{cards}</div>
<h2>Test layers</h2>{layers}
<p class="note">Skipped tests: the live-model end-to-end run (it calls a real model, so it is run by hand), and any test whose
data or dependency is missing. Nothing here calls a model.</p>
{known_html}
<h2>All tests</h2>{''.join(details)}
</main></body></html>"""


def render_plan(ctx):
    path = os.path.join(ROOT, "docs", "QA-VALIDATION-PLAN.md")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    try:
        import markdown
        body = markdown.markdown(text, extensions=["tables"])
    except ImportError:
        body = f"<pre>{html.escape(text)}</pre>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wellspring QA Validation Plan</title><style>{CSS} main{{max-width:1100px}}table{{display:block;overflow-x:auto}}code{{font:13px ui-monospace,Menlo,monospace;background:var(--surface-2);padding:1px 4px;border-radius:4px}}</style></head>
<body><main><nav><a href="index.html">Test report</a></nav>{body}</main></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output folder (default: site)")
    args = ap.parse_args()
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    records, seconds = run_suite()
    ctx = context()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(records, seconds, ctx))
    plan = render_plan(ctx)
    if plan:
        with open(os.path.join(args.out, "qa-plan.html"), "w", encoding="utf-8") as f:
            f.write(plan)
    c = counts(records)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"context": ctx, "seconds": seconds, "counts": c, "tests": records}, f, indent=2)
    bad = sum(c[s] for s in BAD)
    print(f"\nReport written to {args.out}/ : {len(records)} tests, {c[PASS]} passed, {c[SKIP]} skipped, "
          f"{c[XFAIL]} known issues, {bad} failing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
