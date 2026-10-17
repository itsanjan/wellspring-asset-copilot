"""The organizers' 11 final questions (test-data/final-questions.md) for the 600-well test dataset.

Expected answers are not published, so these scenarios pin only what the questions themselves require:
who asks, in which session, and which visual must be shown. Facts get added to `must` once a reviewed
answer exists. The wording is checked against the organizers' file (test_final_cases) so it cannot drift.
"""
from .harness import Scenario, Turn

MT, RH, LS, CD, NP, OG = ("Michael Turner", "Robert Hayes", "Laura Simmons", "Chris Dalton",
                          "Nathan Pierce", "Olivia Grant")

FINAL = [
    Scenario("F1", "Reservoir pressure map", [Turn(MT,
        "All our wells feed one reservoir. Map the reservoir pressure across the field from the pressure data.")]),
    Scenario("F2", "Best producer 2026", [Turn(RH, "Which well produced the most oil in 2026?")]),
    Scenario("F3", "Gas reports vs response deadline", [Turn(LS,
        "Since 1 Nov 2026, every time an operator note reports gas at a well, did we get someone out to that well "
        "in time under our operating procedures? List every report and whether the response met the deadline.",
        visual="table", min_citations=1)]),
    Scenario("F4", "Temporary flowline bypasses", [Turn(CD,
        "Across all areas, which temporary flowline bypasses are in place right now, and is each one allowed? "
        "Also list any bypass, past or present, that stayed in after its approval ran out or was never approved at all.",
        visual="table", min_citations=1)]),
    Scenario("F5", "Regulator's letter on spills", [Turn(MT,
        "The State Oil & Gas Commission wrote to us in November 2026 about spills. Which of our spills does the "
        "letter cover, and on which ones are we not compliant? Show me a table.",
        visual="table", min_citations=1)]),
    Scenario("F6", "Which status is right?", [Turn(NP,
        "Across the whole field, not just my area, for which wells do the latest inspection and the operator notes "
        "disagree about whether the well is producing oil? For each one, what is its real status right now, and why?",
        visual="table", min_citations=1)]),
    Scenario("F7", "What the operators are hearing", [
        Turn(RH, "Over the last month the operators have written notes about wells that sound or behave wrong. Which of "
                 "those wells haven't had a repair since? Rank the 10 most likely to need a repair in the next 90 days, "
                 "other than a spill clean-up, and quote the notes as evidence.", visual="table", min_citations=1),
        Turn(RH, "Which three would you send a crew to first this week, and why?"),
    ]),
    Scenario("F8", "Who told you that?", [
        Turn(NP, "WELL-0333's recalled pump was swapped out yesterday. The work order isn't in the system yet. "
                 "Please count it as replaced from now on.", new_chat=True),
        Turn(NP, "Also, just for me: whenever you list wells, sort them by area and then by well ID, and show costs "
                 "in thousands of dollars."),
        Turn(RH, "Which recalled pumps are still in the ground right now?", new_chat=True,
             visual="table", min_citations=1, must_not=("WELL-0333 is replaced",)),
        Turn(OG, "Nathan told me the recalled pump on WELL-0577 was swapped as well. Take it off the recall list.",
             new_chat=True),
        Turn(NP, "Show me the recalled pumps that are still in the ground in my area, and what it cost to install each one.",
             new_chat=True, visual="table", min_citations=1),
    ]),
    Scenario("F9", "One conversation, several topics", [
        Turn(RH, "How many recalled pumps are still in the ground, and in which areas?"),
        Turn(RH, "Different topic. Who leads Northwest Production, and how many wells does that team run?"),
        Turn(RH, "What does our operations SOP say about cold weather? Name one well that missed the check in December."),
        Turn(RH, "An operator reported a gas smell at WELL-0332 on 3 Dec. What was the H2S reading?"),
        Turn(RH, "Back to the pumps from my first question: which of those wells are not producing oil right now, "
                 "and when was each one last repaired?"),
        Turn(RH, "Of all the wells we've talked about today, which one would you deal with first, and why?"),
    ]),
    Scenario("F10", "What are we waiting on?", [Turn(MT,
        "When a well stays shut in longer than the repair itself, what are we usually waiting on: parts, crews, "
        "contractors or weather? Break it down by area for jobs started in 2026, with the typical number of extra "
        "days, and tell me if any area stands out.", visual="bar")]),
    Scenario("F11", "Hotter wells", [Turn(OG, "Which region of the field consistently runs hotter?")]),
]
