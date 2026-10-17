# Wellspring: flows and diagrams

Diagrams are [Mermaid](https://mermaid.js.org/), which GitHub renders in the browser. Each
one has a "verify" note listing what to check against the problem statement and example
questions (Q1-Q18). If a diagram looks wrong to you, the design is wrong, so say so before
we build it.

| # | Diagram | Answers |
|---|---|---|
| 1 | System components | What are the parts and who talks to whom? |
| 2 | Question to answer, end to end | What happens when someone asks Q5? |
| 3 | Effective truth resolution | How do raw data, corrections and notes combine? |
| 4 | Feedback and scope | How does a correction or a rule reach other users, and only the right wells? |
| 5 | Routing what a user says | Is it a question, a fact, a correction, a rule or a claim? |
| 6 | Conflict handling | How do we avoid silently picking a side (Q17, Q18)? |
| 7 | Data model | What is stored, and what is only derived? |
| 8 | Response contract | What does every answer contain? |

---

## 1. System components

```mermaid
flowchart TB
    subgraph UI["React chat UI"]
        picker["User picker"]
        chat["Chat thread"]
        visuals["Inline visuals: card, table, line, bar, map, heatmap"]
        cites["Citations and correction badges"]
    end

    subgraph API["FastAPI backend"]
        agent["Agent orchestrator: plan, call tools, verify, compose"]
        router["Message router: question or statement"]
        tools["Typed tools"]
        render["Visual spec builder"]
    end

    subgraph STORE["Layered store"]
        raw[("L0 Raw records, immutable")]
        derived[("L1 Derived views")]
        docs[("L2 Documents and report facts")]
        overlay[("L3 Overlay: corrections, rules, notes, claims")]
        priv[("L4 Private conversations")]
    end

    llm["LLM provider, configurable"]
    loader["Loader: ingest any dataset in the same format"]

    picker --> chat
    chat -->|"question + user_id"| router
    router --> agent
    agent <-->|"tool calls"| llm
    agent --> tools
    tools --> derived
    tools --> docs
    tools --> overlay
    tools --> priv
    derived --> raw
    agent --> render
    render --> visuals
    agent --> cites
    loader --> raw
    loader --> docs
```

**Verify:** the LLM never touches storage directly, only through tools. Private data (L4) is
reachable only with the caller's `user_id`. A new dataset only needs the loader to run.

---

## 2. Question to answer, end to end

Example: _"How many days was WELL-0001 down in 2025?"_ (Q5, expected 21 days).

```mermaid
sequenceDiagram
    actor U as User (Michael Turner)
    participant UI as Chat UI
    participant A as Agent
    participant L as LLM
    participant T as Tools
    participant S as Store (effective view)

    U->>UI: "How many days was WELL-0001 down in 2025?"
    UI->>A: question, user_id
    A->>S: load private memory and shared notes for WELL-0001
    A->>L: question + tool schemas + memory + notes
    L-->>A: call downtime(scope=WELL-0001, 2025-01-01..2025-12-31)
    A->>T: downtime(...)
    T->>S: inspections for window, with corrections applied
    T->>S: read producing threshold for the well type
    S-->>T: weekly readings for 2025, threshold 15 C (T200)
    T-->>A: 3 non-producing inspections, 21 days, record IDs
    L-->>A: call search_docs("causes of downtime 24 Jul 2025")
    A->>T: search_docs / repairs lookup
    T-->>A: MNT-0001-021, MNT-0001-023 with report excerpts
    A->>L: tool results
    L-->>A: composed answer + visual choice (none needed)
    A->>A: verify every number came from a tool result
    A-->>UI: answer, evidence list, visual spec
    UI-->>U: "21 days" + cited inspections and repairs
    A->>S: save turn to U's private memory (wells, window, topic)
```

**Verify:** the 21 comes from the `downtime` tool, not from the model. The 24 Jul reading
(51.6 C wellhead, 43.1 C air) is judged on the difference, not the absolute value. The turn is
saved privately so Q11-style follow-ups work.

---

## 3. Effective truth resolution

How any tool decides what is "true" for a record. Original data is never changed.

```mermaid
flowchart TD
    start(["Tool reads a value for inspection I of well W"]) --> raw["Read raw record from L0"]
    raw --> c1{"Formal correction note for I?"}
    c1 -- yes --> useNote["Use the corrected detection or value; keep original attached"]
    c1 -- no --> c2{"Overlay correction for I?<br/>scope: this record"}
    c2 -- yes --> useOv["Use the overlay value; record author, reason, time"]
    c2 -- no --> c3{"Overlay rule matching W's well type?<br/>scope: type"}
    c3 -- yes --> useRule["Apply the rule at type scope only<br/>and say it is the user's rule, not the manual"]
    c3 -- no --> useRaw["Use the raw value"]
    useNote --> derive
    useOv --> derive
    useRule --> derive
    useRaw --> derive
    derive["Apply the manual rule: producing = wellhead minus ambient >= type threshold"] --> flags["Attach flags: stale recorded_status, disagreeing inspector note, unconfirmed claims"]
    flags --> out(["Return value + evidence IDs + flags"])
```

**Verify:** a plain note that disagrees (Q18) does not count as a correction, so the detection
stands and the disagreement is shown instead. A correction beats raw data (Q4, Q12). Unconfirmed
user claims never change the value, only add a flag (Q17).

---

## 4. Feedback and scope

Q12 (raw reading corrected), Q13 (AI estimate corrected) and Q14 (rule for a well type), then
a different user asking later.

```mermaid
sequenceDiagram
    actor M as Michael Turner
    participant A as Agent
    participant O as Overlay store (L3)
    actor J as Jessica Reed (another user)

    M->>A: "15 Oct reading for WELL-0164 is wrong, re-measured at 41.0 C"
    A->>A: classify: correction, target INS-0164-0205, scope: one record
    A->>O: append {target, original 24.0, new 41.0, author, reason, time, status confirmed}
    A->>A: re-evaluate WELL-0164: 21.1 C above air, T200 limit 15, producing
    A->>A: conflict check: inspector note "Pump jack not moving"
    A-->>M: recorded - producing - but note disagrees, please confirm

    M->>A: "For all T350 wells, RCA after 2 pump jack repairs, not 3"
    A->>A: classify: rule, scope: well type T350
    A->>O: append {rule, applies_to T350, threshold 2, author, time}
    A-->>M: recorded for T350 only, will be labelled as your rule

    J->>A: "Is WELL-0164 producing oil right now?"
    A->>O: read corrections for WELL-0164
    O-->>A: M's correction, with original and reason
    A-->>J: yes, 41.0 C after correction by Michael Turner - inspector note disagrees

    J->>A: "Which NE-07 wells need a pump jack RCA?"
    A->>O: read rules for each well type
    O-->>A: T350 threshold 2 (M's rule), T200 stays 3 (manual)
    A-->>J: WELL-0108, 0120, 0121, 0138 - 0102 (T200) not added
```

**Verify:** the correction is global to all users but narrow in scope. The T350 rule never touches
WELL-0102 (T200). The original value is retained and shown. Every overlay entry has an author and reason.

---

## 5. Routing what a user says

Not every message is a question. This decides where it goes.

```mermaid
flowchart TD
    msg(["User message"]) --> k{"What is it?"}
    k -- question --> ask["Run the agent: tools, evidence, visual"]
    k -- "refers to past chats" --> mem["resolve_entities from THIS user's private memory only"]
    mem --> found{"Found?"}
    found -- yes --> ask
    found -- no --> none["Say there is no earlier conversation with this user; do not borrow another user's"]
    k -- "statement about a well" --> st{"Contradicts latest data?"}
    st -- yes --> claim["Store as unconfirmed claim + show the contradiction + ask to confirm"]
    st -- no --> note["Store as shared well note, attributed"]
    k -- "correct raw data" --> corr["Overlay correction, scope: record"]
    k -- "correct AI analysis" --> corr2["Overlay correction, scope: record, keep AI estimate"]
    k -- "rule for a well type" --> rule["Overlay rule, scope: type"]
    corr --> chk["Check for disagreeing notes, then reply"]
    corr2 --> chk
    rule --> chk
    claim --> chk
    note --> chk
```

**Verify:** the routing is the riskiest LLM step, so it gets its own tests (Q11, Q12, Q13, Q14, Q16,
Q17). Shared knowledge versus private memory is decided here, and private is enforced in code.

---

## 6. Conflict handling

Q17 (user claims vs data) and Q18 (two notes disagree).

```mermaid
flowchart TD
    in(["New claim, or an answer that touches existing notes"]) --> s1{"Claim vs latest inspection derived status"}
    s1 -- agree --> s2{"Claim vs other users' claims or notes"}
    s1 -- disagree --> show1["Show: claim, the inspection that contradicts it, which side the data supports"]
    s2 -- agree --> ok["Answer normally and cite"]
    s2 -- disagree --> formal{"Is either a formal correction?"}
    formal -- yes --> win["The correction wins; mention the other view"]
    formal -- no --> both["Show both sources side by side; detection stands if repair or HSE sample supports it"]
    show1 --> keep["Keep claim on record as unconfirmed; ask for confirmation"]
    both --> keep2["Keep both on record; no silent pick"]
    keep --> done(["Reply"])
    keep2 --> done
    win --> done
    ok --> done
```

**Verify:** Q17 Tyler Hughes ("running fine") gets told the 15 Oct inspection shows 0.4 C above air and that it matches Michael Turner's
note, not his claim. Q18 answers "yes" but shows Ryan Brooks's and Chris Dalton's notes.

---

## 7. Data model

```mermaid
erDiagram
    WELL ||--o{ INSPECTION : has
    WELL ||--o{ MAINTENANCE : has
    WELL ||--o{ REGULATORY_EVENT : has
    WELL ||--o{ NOTE : has
    WELL }o--|| WELL_TYPE : "is of"
    WELL }o--|| AREA : "lies in"
    AREA }o--|| TEAM : "owned by"
    USER }o--|| TEAM : "belongs to"
    WELL_TYPE ||--o{ RULE_PARAM : "defines (parsed from manual)"
    MAINTENANCE ||--o| REPORT : "report_file"
    REPORT ||--o{ REPORT_FACT : "extracted"
    INSPECTION ||--o{ MAINTENANCE : "triggers"
    INSPECTION ||--o{ REGULATORY_EVENT : "related to"
    INSPECTION ||--o{ OVERLAY_ENTRY : "target of"
    WELL_TYPE ||--o{ OVERLAY_ENTRY : "target of (rule)"
    USER ||--o{ OVERLAY_ENTRY : authors
    USER ||--o{ CONVERSATION : owns
    CONVERSATION ||--o{ TURN : contains
    TURN ||--o{ ENTITY_REF : mentions
    DOC_CHUNK }o--|| DOCUMENT : "part of"

    INSPECTION {
        string inspection_id PK
        string asset_id FK
        datetime inspected_at
        float ambient_temp_c
        float wellhead_temp_c
        bool spill_detected
        float spill_est_volume_bbl
    }
    OVERLAY_ENTRY {
        string entry_id PK
        string kind "correction, rule, claim, note"
        string target_ref "record or well type"
        string scope "record, well, type"
        string original_value
        string new_value
        string author_user_id FK
        string reason
        datetime created_at
        string status "confirmed, unconfirmed, superseded"
    }
    REPORT_FACT {
        string maintenance_id FK
        string protocol "SR-02 etc"
        string result
        float contained_hours
    }
    RULE_PARAM {
        string well_type
        string name "producing_delta_c, rca_repairs, rca_window_months"
        float value
        string source_section
    }
```

**Verify:** "producing" and "downtime" are views over INSPECTION plus RULE_PARAM, never stored columns.
OVERLAY_ENTRY is the only table users can write into besides CONVERSATION. Raw tables are read-only.

---

## 8. Response contract

Every answer has the same shape, so the UI and the tests can both rely on it.

```mermaid
classDiagram
    class Answer {
        +string text
        +Evidence[] evidence
        +VisualSpec visual
        +Flag[] flags
        +Usage usage
    }
    class Evidence {
        +string kind // inspection, repair, report, regulatory, note, sop, manual, overlay
        +string ref_id
        +string excerpt
        +string source_path
    }
    class VisualSpec {
        +string type // card, table, line, bar, map, heatmap, none
        +object data
        +string title
        +bool user_requested
    }
    class Flag {
        +string kind // stale_status, conflict, unconfirmed_claim, correction_applied, no_recorded_reason
        +string message
        +string[] sources
    }
    class Usage {
        +int tool_calls
        +int tokens_in
        +int tokens_out
        +float cost_usd
    }
    Answer "1" --> "*" Evidence
    Answer "1" --> "1" VisualSpec
    Answer "1" --> "*" Flag
    Answer "1" --> "1" Usage
```

**Verify:** "cites the records and documents it used" is enforced structurally (no evidence, no numeric
claim), and `Usage` gives the cost numbers the write-up needs.

---

## How to use this to verify the design

1. Pick three questions, for example Q5, Q12 and Q17, and trace each through diagrams 2, 4 and 6.
2. Look for any step where the LLM, not a tool, decides a number or a scope. That is a risk.
3. Look for any path where one user's private data can reach another. There should be none.
4. Check that no diagram mentions a specific well or the sample's numbers except as an example.
