import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "./api";
import type { Answer, Conversation, FeedbackEntry, Flag, Turn, User } from "./types";
import { VisualView } from "./visuals/Visual";

const SUGGESTIONS = [
  "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show me its status card.",
  "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.",
  "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.",
  "Show a heatmap of 2026 spills by geographic area and month.",
  "Which rules has WELL-0001 broken in the last 12 months, and what is still open?",
];

// Status colors carry state only, always with an icon and a label (never color alone).
const FLAG_STYLE: Record<string, { label: string; icon: string; color: string }> = {
  conflict: { label: "Conflict", icon: "⇄", color: "var(--serious)" },
  unconfirmed_claim: { label: "Unconfirmed", icon: "?", color: "var(--warning)" },
  stale_status: { label: "Out of date", icon: "!", color: "var(--warning)" },
  no_recorded_reason: { label: "No recorded reason", icon: "?", color: "var(--warning)" },
  report_outdated: { label: "Report outdated", icon: "!", color: "var(--serious)" },
  missing_rule: { label: "Missing rule", icon: "!", color: "var(--critical)" },
  containment_breach: { label: "Rule broken", icon: "!", color: "var(--critical)" },
  status_changed: { label: "Changed", icon: "→", color: "var(--ink-2)" },
  correction_applied: { label: "Corrected", icon: "✎", color: "var(--ink-2)" },
  other_statement: { label: "Also on record", icon: "”", color: "var(--ink-2)" },
  data_note: { label: "Note", icon: "i", color: "var(--ink-2)" },
  context: { label: "Context", icon: "i", color: "var(--ink-2)" },
};

export default function App() {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("U-201");
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [convId, setConvId] = useState<string | undefined>();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [shared, setShared] = useState<FeedbackEntry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState<{ ready: boolean; detail: string; name: string }>({ ready: true, detail: "", name: "" });
  const [menu, setMenu] = useState(false);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.users().then(setUsers);
    api.health().then((h) => setModel({ ready: h.model_ready, detail: h.model_detail, name: h.provider ? `${h.provider} · ${h.model}` : "" }))
      .catch((e) => setModel({ ready: false, detail: String(e), name: "" }));
    api.feedback().then(setShared);
  }, []);
  useEffect(() => { api.conversations(userId).then(setConvs); newChat(); }, [userId]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns, busy]);
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  function newChat() { setConvId(undefined); setTurns([]); setMenu(false); }

  async function openConv(id: string) {
    const c = await api.conversation(id, userId);
    setConvId(id);
    setTurns(c.turns.map((t) => ({ role: t.role, text: t.text, answer: t.answer })));
    setMenu(false);
  }

  async function send(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    setInput("");
    setTurns((t) => [...t, { role: "user", text: question }]);
    setBusy(true);
    try {
      const a = await api.ask(userId, question, convId);
      setConvId(a.conversation_id);
      setTurns((t) => [...t, { role: "assistant", text: a.text, answer: a }]);
      api.conversations(userId).then(setConvs);
      api.feedback().then(setShared);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", text: "", error: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  const me = users.find((u) => u.user_id === userId);
  return (
    <div className="app">
      <header className="topbar">
        <button className="ghost menu-btn" onClick={() => setMenu(!menu)} aria-label="Menu">☰</button>
        <div className="brand">Wellspring<small>Asset Intelligence Copilot</small></div>
        <div className="spacer" />
        {model.name && <span className="empty" title="Model in use">{model.name}</span>}
        <label className="picker">
          <span>User</span>
          <select value={userId} onChange={(e) => setUserId(e.target.value)}>
            {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name} — {u.role.replace(/_/g, " ")}</option>)}
          </select>
        </label>
        <button className="ghost" onClick={() => setTheme(theme === "system" ? "dark" : theme === "dark" ? "light" : "system")}
          title="Theme">{theme === "system" ? "Auto" : theme === "dark" ? "Dark" : "Light"}</button>
      </header>
      {model.ready ? <div style={{ gridColumn: "1 / -1" }} /> : <div className="banner">The model is not available ({model.detail}), so questions will fail. History and shared knowledge still load.</div>}

      <aside className={`sidebar${menu ? " open" : ""}`}>
        <button className="primary" onClick={newChat}>New chat</button>
        <section>
          <h3>{me ? `${me.name.split(" ")[0]}'s conversations` : "Conversations"}</h3>
          {convs.length === 0 && <p className="empty">No conversations yet.</p>}
          {convs.map((c) => (
            <button key={c.conversation_id} className={`conv${c.conversation_id === convId ? " active" : ""}`}
              onClick={() => openConv(c.conversation_id)} title={c.title}>{c.title}</button>
          ))}
        </section>
        <section>
          <h3>Shared knowledge</h3>
          {shared.length === 0 && <p className="empty">Corrections, rules and notes people add appear here for everyone.</p>}
          {shared.map((s) => <SharedItem key={s.entry_id} s={s} />)}
        </section>
      </aside>

      <main className="main">
        <div className="thread">
          <div className="thread-inner">
            {turns.length === 0 && (
              <div className="welcome">
                <h2>Ask about your wells</h2>
                <p>Answers come from inspections, repairs, reports, regulator events, notes and the manuals, with the records cited.</p>
                <div className="suggestions">{SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
              </div>
            )}
            {turns.map((t, i) => t.role === "user"
              ? <div key={i} className="msg-user">{t.text}</div>
              : <BotMessage key={i} turn={t} />)}
            {busy && <div className="msg-bot thinking">Checking the records…</div>}
            <div ref={end} />
          </div>
        </div>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <div className="composer-inner">
            <textarea value={input} placeholder={`Ask as ${me?.name ?? userId}…`} rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="primary" disabled={busy || !input.trim()}>Ask</button>
          </div>
        </form>
      </main>
    </div>
  );
}

function SharedItem({ s }: { s: FeedbackEntry }) {
  const what = s.kind === "correction" ? `${s.target_ref} ${s.field}: ${s.original_value} → ${s.new_value}`
    : s.kind === "rule" ? `All ${s.target_ref} wells: ${s.field} ${s.original_value} → ${s.new_value}`
    : `${s.target_ref}: ${s.text}`;
  return (
    <div className="shared">
      <b>{s.entry_id}</b> {s.kind}{s.status === "unconfirmed" ? " (unconfirmed)" : ""} · {s.author}
      <div>{what}</div>
    </div>
  );
}

function BotMessage({ turn }: { turn: Turn }) {
  if (turn.error) return <div className="msg-bot error">Could not answer: {turn.error}</div>;
  const a = turn.answer as Answer | undefined;
  const flags = dedupe(a?.flags ?? []);
  return (
    <div className="msg-bot">
      <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.text}</ReactMarkdown></div>
      {a?.visual && <VisualView visual={a.visual} />}
      {flags.length > 0 && (
        <div className="flags">
          {flags.map((f, i) => {
            const st = FLAG_STYLE[f.kind] ?? { label: f.kind, icon: "i", color: "var(--ink-2)" };
            return (
              <div key={i} className="flag">
                <span className="icon" style={{ color: st.color }} aria-hidden>{st.icon}</span>
                <b>{st.label}</b><span>{f.message}</span>
              </div>
            );
          })}
        </div>
      )}
      {a && a.evidence.length > 0 && (
        <details className="evidence">
          <summary>Evidence: {a.evidence.length} records and documents</summary>
          <ul>
            {a.evidence.map((e, i) => (
              <li key={i}><span className="badge">{e.kind}</span><span className="ref">{e.ref}</span>{e.detail}
                {e.source && <span className="src"> · {e.source}</span>}</li>
            ))}
          </ul>
        </details>
      )}
      {a?.usage && (
        <div className="meta">
          {a.tools_used.map((t) => t.tool).join(" → ") || "no tools"} · {a.usage.model} · {a.usage.model_calls} model calls ·
          {" "}{(a.usage.input_tokens + a.usage.cache_read_tokens).toLocaleString()} in / {a.usage.output_tokens.toLocaleString()} out · {a.usage.cost_usd ? `$${a.usage.cost_usd.toFixed(4)}` : "free"}
        </div>
      )}
    </div>
  );
}

function dedupe(flags: Flag[]) {
  const seen = new Set<string>();
  // The same issue can be reported by two tools in different words; key on kind + first record.
  return flags.filter((f) => {
    const key = `${f.kind}|${f.refs?.[0] ?? f.message}`;
    return seen.has(key) ? false : (seen.add(key), true);
  });
}
