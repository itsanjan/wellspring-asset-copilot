"""The organizers' 18 example questions (problem-statement/03-evaluation-queries.md) as scored scenarios.

A1 (Q1-Q10) are independent. The stateful scenarios (Q11-Q18) must run in this order on a clean app.
"""
from .harness import Scenario, Turn

MT, JR, LS, JM, TH = "Michael Turner", "Jessica Reed", "Laura Simmons", "Jake Morales", "Tyler Hughes"


def one(id, title, q, **kw):
    return Scenario(id, title, [Turn(MT, q, **kw)])


Q6_TEXT = "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart."
Q2_TEXT = "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table."

A1 = [
    one("Q1", "Is WELL-0001 producing? status card",
        "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show me its status card.",
        must=(("not producing", "no,", "isn't producing", "is not producing"), "INS-0001-0203", "Ryan Brooks", "3.2", "CTX-0001-003"),
        visual="card", min_citations=1),
    one("Q2", "NE Block 8 not producing, table", Q2_TEXT,
        must=("WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175", "MNT-0170-031", "MNT-0172-028", ("no recorded reason", "no reason")),
        visual="table", min_citations=1),
    one("Q3", "WELL-0001 13-week temperature line chart",
        "Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks, and mark when it was not producing oil.",
        must=(("2026-10-08", "8 oct"), ("2026-10-15", "15 oct")), visual="line"),
    one("Q4", "WELL-0001 spills in 4 years, table",
        "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? List them in a table.",
        must=("15.1", "6.8", "2.6"), visual="table", min_citations=1),
    one("Q5", "WELL-0001 downtime in 2025",
        "How many days was WELL-0001 down in 2025?",
        must=("21", "MNT-0001-021", "MNT-0001-023"), min_citations=1),
    one("Q6", "Top 3 downtime NE Block 7, bar chart", Q6_TEXT,
        must=("WELL-0121", "49", "WELL-0138", "WELL-0107", "42"), visual="bar"),
    one("Q7", "NE Block 8 real spills, map",
        "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.",
        must=("WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"), visual="map"),
    one("Q8", "2026 spills by area x month, heatmap",
        "Show a heatmap of 2026 spills by geographic area and month.",
        must=("43", "35", "37", "26"), visual="heatmap"),
    one("Q9", "Rules WELL-0001 broke in 12 months",
        "Which rules has WELL-0001 broken in the last 12 months, and what is still open?",
        must=("SR-01", "SR-02", "SR-03", "RC-04", "REG-0001-002", "5000"), min_citations=1),
    one("Q10", "SW Block 24 spills not contained in time",
        "In Southwest Block 24, which spills in the last 90 days were not contained within the SOP time limit?",
        must=("WELL-0082", "WELL-0100", "7.1", "8.1"), visual="table", min_citations=1),
]

STATEFUL = [
    Scenario("Q11", "Remembers yesterday's conversation", [
        Turn(MT, Q6_TEXT, must=("WELL-0121",)),
        Turn(MT, "For the three wells we discussed yesterday, what have repairs cost this year?", new_chat=True,
             must=("25794", "46942", "5176")),
    ]),
    Scenario("Q12", "Correct wrong raw data", [
        Turn(MT, "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; "
                 "the field crew re-measured the wellhead at 41.0 °C.",
             must=("INS-0164-0205", "41.0", "producing", "pump jack not moving", "24.0")),
        Turn(MT, Q2_TEXT, must=("WELL-0170", "WELL-0172", "WELL-0175", "WELL-0164"), visual="table"),
    ]),
    Scenario("Q13", "Correct the AI's analysis", [
        Turn(MT, "The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 bbl.",
             must=("INS-0151-0201", "5.4", "reportable", "SR-01", ("2026-11-23", "23 nov"), "MNT-0151-025")),
    ]),
    Scenario("Q14", "Scoped rule for a well type", [
        Turn(MT, "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?",
             must=("WELL-0108", "WELL-0121", "WELL-0138")),
        Turn(MT, "Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, not 3."),
        Turn(MT, "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?", new_chat=True,
             must=("WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"),
             # WELL-0102 (T200, 2 repairs) must not be added; the strict check can fail if the answer merely mentions it.
             must_not=("WELL-0102",)),
    ]),
    Scenario("Q15", "Correction reaches other users", [
        Turn(JR, "Is WELL-0164 producing oil right now?", must=("yes", "41.0", "michael turner", "pump jack not moving")),
        Turn(LS, "Which spills in Northeast Block 8 in the last 90 days needed a report to the regulator?",
             must=("WELL-0151", "5.4", ("overdue", "no written notice", "none is recorded"))),
    ]),
    Scenario("Q16", "Shared knowledge, private memory", [
        Turn(JM, "FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover."),
        Turn(MT, "Is any downtime planned for WELL-0007?", new_chat=True, must=(("17 oct", "2026-10-17"), "jake morales")),
        Turn(JR, "For the three wells we discussed yesterday, what have repairs cost this year?",
             must=(("no earlier", "no previous", "don't have", "do not have", "no record", "not have"),),
             must_not=("25794", "46942", "5176")),
    ]),
    Scenario("Q17", "People disagree with the data", [
        Turn(MT, "WELL-0175 has been shut in for planned work since 14 Oct."),
        Turn(TH, "WELL-0175 is running fine.", must=("0.4", ("unconfirmed", "not confirmed"))),
        Turn(JM, "WELL-0001 is back online since 14 Oct.", must=("CTX-0001-003", ("unconfirmed", "not confirmed"))),
    ]),
    Scenario("Q18", "People disagree about the AI's analysis", [
        Turn(MT, "Was the 20 Aug spill at WELL-0101 real oil?",
             must=("yes", "CTX-0101-002", "CTX-0101-003", "INS-0101-0197", "MNT-0101-029")),
    ]),
]
