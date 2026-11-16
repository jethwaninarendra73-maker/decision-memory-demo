# Enterprise Decision Intelligence & Institutional Memory Agent — Hackathon MVP

> **SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.** All data is fictional. Nothing here connects to Teams, Jira, email or any company system, and nothing executes a business action.

An offline-first Streamlit app that walks a Pricing Director through one question, *"Should we increase the freight surcharge by 5% for European customers next quarter?"*, using six logical agents: Decision Capture, Institutional Memory, Impact Analysis, Scenario Simulation, Recommendation and Governance.

**Build status: Stage 11 of 15.** All six agents work end to end. Agent 1 captures a decision from a synthetic transcript. Agent 2 answers the question with ranked, cited precedents and a decision graph. Agents 3 to 5 assess impact, compare +3/+5/+7% and recommend a guarded option. Agent 6 records a simulated approval, tracks the pilot for 60 days and writes its outcome back to memory. Everything is filtered for the selected demo role, cited, and audited. A guided panel walks a presenter through the five-minute demo. The optional LLM mode and the demo kit come in later stages. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Requirements

- macOS with the Xcode Command Line Tools (`xcode-select --install`)
- Python 3.11 or later (developed on 3.15.2)
- Network access once, to install packages. After that the app runs fully offline with no API key.

## Set up (macOS, from the repo root)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_demo.py
```

## Run

```bash
source .venv/bin/activate
streamlit run app.py
```

The app opens at http://localhost:8501. Stop it with `Ctrl+C`.

## Guided demo (five minutes, six steps)

A **Guided demo** panel sits above the stage tabs. It is on by default; turn it off with the **Guided demo** switch in the sidebar to use the tabs freely.

| Step | Time | Where | What happens |
|------|------|-------|--------------|
| 1 · Hook | 0:00–0:30 | the panel | Three synthetic artifacts, quoted as memory cites them: the France decision call (Teams-style transcript, SRC-006), the European pricing guardrails (steering deck, SRC-014) and the Germany contract-customer escalations (Jira-style ticket, SRC-002). Each opens in full in the Evidence explorer. |
| 2 · Capture | 0:30–1:15 | tab 1 | Extract the demo transcript, review, confirm. |
| 3 · Ask | 1:15–2:15 | tab 2 | Cited precedents, the failed one as a warning, the lesson from another region, the graph. |
| 4 · Analyze | 2:15–3:30 | tabs 3 and 4 | Impact labels, tick the contract-customer exclusion, compare +3/+5/+7%. |
| 5 · Recommend | 3:30–4:30 | tab 5 | The advisory card, the guardrail exclusion, key risks, cited reasons. |
| 6 · Close the loop | 4:30–5:00 | tabs 6 and 2 | Approve (simulated), record week 1, close the pilot, see the outcome in memory. |

- **Next step** and **Back** move between steps and open the step's tab. Hiding the guide keeps its step. Each step also has buttons for the tabs it uses. Picking a step directly works too. The tabs remember which one is open, so switching tabs reruns the app (about 0.3 s locally).
- Each step lists what to do and what to say. The "where does the figure come from" talk track in step 4 is built from the scenario figures on screen, so it follows any edited input.
- **Live checks** show what the app currently holds for the active role, for example "Precedents returned: …", "High-volume contract customers are excluded in the draft proposal" or "Outcome OUT-015 is in memory". They are read from the tabs on every rerun; they never change anything.
- The guide reads only through the role-filtered view, like the tabs. It shows nothing a role cannot see.
- The steps, checks and talk tracks are in `src/decision_memory/demo_guide.py` (pure, tested); the panel is `src/decision_memory/ui/guide.py`.

Every stage tab starts with the label **SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION**, and the same label stays at the top of the page.

## Demo data: seed and reset

```bash
python scripts/seed_demo.py     # load the synthetic baseline; safe to run repeatedly
python scripts/reset_demo.py    # delete data/demo.db and rebuild the baseline
```

- **Seed** upserts the fixture records by ID. Running it twice changes nothing, and it leaves records you created in the app alone.
- **Reset** removes everything created in the app, including approvals, pilot trackers, generated governance records and their outcomes. It refuses to delete a file this app did not create.
- Both accept `--db PATH` to target another file.

Baseline counts after either command:

| Table | Rows |
|-------|------|
| source_documents | 22 |
| decisions | 16 |
| entities | 47 |
| relations | 147 |
| outcomes | 14 |
| audit_events | 1 |
| proposals, scenario_results, recommendations, approvals, milestones, kpi_updates | 0 |

The fixtures live in [data/fixtures/](data/fixtures/README.md). `data/demo.db` is generated and git-ignored.

## Agent 1: capture a decision

The first tab turns a meeting transcript into a decision that a human confirms.

Demo steps (about 45 seconds):

1. Run `python scripts/reset_demo.py`, then `streamlit run app.py`.
2. Open tab **1 · Decision Capture**. **Demo transcript** is selected and the transcript is shown.
3. Click **Extract decision**. The **Extracted decision card** appears with each field, its confidence, the character offsets of its evidence, and any flags. **Not treated as decisions** lists the eight lines that were left out and why.
4. In **Review**, change anything you like, then click **Confirm and save to memory** (or **Reject draft**, which saves nothing).
5. A green banner confirms `DEC-017`, and its transcript opens as `SRC-023` in the Evidence explorer.

The demo transcript is [data/fixtures/capture/demo_transcript.md](data/fixtures/capture/demo_transcript.md). You can also **Paste text** or **Upload** a `.txt` or `.md` file. Use fictional text only.

How a transcript is read:

- Header lines `Meeting:`, `Date:` (YYYY-MM-DD) and `Attendees:`.
- A decision is a line starting with `DECISION:`. Speech such as "we should" or "we decided", and lines marked `NO DECISION:`, `PARKED:`, `ACTION:` or `PROPOSAL:`, are never captured.
- `OWNER:`, `REGION:`, `MARKET:`, `SEGMENT:`, `RATIONALE:`, `ALTERNATIVE:`, `RISK:` and `EXPECTED OUTCOME:` lines after a decision describe it.
- A field that is absent, a placeholder such as "TBC", or contradictory is left blank and flagged. Owner, date, region, market and segment must be filled in by the reviewer before confirming.
- Confidence is rule-based (0.9 stated on its own line, 0.6 derived or inconsistent, 0.3 hedged wording). It is not a model prediction, and it never confirms anything by itself.

Pasted and uploaded text is only ever read as characters. It is not run, rendered, sent anywhere or followed as an instruction. Capturing the same transcript twice is refused, so reset before each rehearsal.

## Precedent search (Agent 2)

Tab **2 · Institutional Memory** opens with **Ask institutional memory**. The demo question is filled in; type any other question to search the decisions the selected role can see.

- The region and surcharge rate are read from the question only when it states exactly one of each (for example "European" and "5%"). They appear under **Filters read from the question**, where they can be changed, along with an optional customer segment and the date recency is measured from.
- Search matches words, helped by a short, visible list of equivalences (for example "price uplift" → "surcharge increase", "client" → "customer", "trial" → "pilot", "EU" → "Europe"). The equivalences a question triggered are listed under it; the full list is `PHRASES` and `SYNONYMS` in `src/decision_memory/precedent_search.py`.
- Each decision gets four scores between 0 and 1, all shown with how they were worked out: **lexical overlap** (share of the question's terms found in the decision, rarer terms counting more; words that only name the asked region are left to the match score, so they are not counted twice), **region / segment / rate match**, **outcome relevance** (1 if an outcome has been observed, 0.3 if not), and **recency** (halves every 730 days). The total is 0.40 × lexical + 0.30 × match + 0.15 × outcome + 0.15 × recency. If the question sets no region, segment or rate, the match weight is shared across the other three.
- Up to five decisions are returned, plus up to two failed precedents that qualify but rank below them: a newer, closer decision (such as an approved pilot) never pushes a failure warning out of view. A decision must share enough of the question's terms (lexical at least 0.5) and score at least 0.45, so a matching region or rate alone never makes something a precedent. If nothing qualifies, the answer is "Insufficient verifiable evidence." and nothing is returned.
- When the question names a region, up to two decisions from **other** regions that clear the same two bars are shown under **Lessons from other regions**. They are contrast, not precedents for the asked region, and they are not cited as such.
- Both bars can be changed with sliders under **Filters read from the question**. The defaults are checked against 21 hand-labelled questions in `tests/search_eval.json` (demo, reworded and specific questions that should find given decisions, and off-topic ones that should abstain). On that set, every setting with lexical 0.40–0.575 and score 0.20–0.60 gives the expected answers, and a test keeps every setting within ±0.05 of the defaults passing. The labels are the author's reading of the fictional data, not a benchmark.
- Every result cites what was decided and what happened, with `[SRC-…]` excerpts. A failed precedent is shown with a warning: it is a lesson, not a template.
- **Every decision this role can see, ranked** lists all scores, including the ones that did not qualify.
- Precedents (yellow) and lessons from other regions (purple) are highlighted in the graph below, and **Focus → Search results** shows just them and their neighbours.

For the demo question the precedents are DEC-009 (UK and Ireland), DEC-007 (Italy), DEC-004 (France), DEC-011 (Germany 3%) and DEC-001 (Germany 6%, failed), and the lesson from another region is DEC-014 (Singapore copied the France 5% surcharge without a local pilot, and failed). Exact scores move slightly with the recency date.

## Decision graph (Agent 2)

Below the search, the tab draws memory as a network of decisions, outcomes, markets, segments, customer groups, systems, risks, initiatives, stakeholders and the sources they cite.

- The graph is a projection of the SQLite database, not a second store. It is rebuilt on every view, so a new decision or an updated outcome appears straight away.
- It is built only from records the selected role may see. A hidden decision takes its outcomes, links and any entity reachable only through it out of the graph, and a source that only hidden records cite is left out too.
- Every edge is typed. Stored relations keep their type (`SUPERSEDES`, `CONTRADICTS`, `SIMILAR_TO`, `INFORMED_BY`, `APPLIES_TO`, `AFFECTS`, `HAS_RISK`, `OWNED_BY`, `INVOLVES`, `PART_OF`). Two more are derived: `SUPPORTED_BY` from a decision or outcome to each source it cites, and `RESULTED_IN` from a decision to its outcome. A graph with an edge to a missing node cannot be built.
- **Show node kinds** picks what to draw; **Focus** shows one decision and everything one hop from it; **View → Tables** lists every node and edge. The tables also appear automatically if the network cannot be drawn.

## Impact analysis (Agent 3)

Tab **3 · Impact Analysis** takes the question and the precedents from tab 2 and lists what the proposal would touch: affected teams, systems, customer groups and risks, plus an illustrative cost and effort range.

- The **draft proposal** is built from the question: its region, the quarter after the search date (2027-Q1 for 13 Nov 2026), and options +3 / +5 / +7 % for Agent 4 to compare. The one choice is whether to exclude high-volume contract customers. The draft is not saved, and nothing is sent, priced or changed.
- Every item comes from a rule in `DEFAULT_RULES` (`src/decision_memory/agents/impact_agent.py`). A rule lists terms; it matches a line in a precedent's own sources (the ones its decision and outcome cite) that contains a word starting with one of them. A hyphen joins words, so "dispute" does not match "invoice-dispute".
- An item is **Source-supported** when at least one line matches, and each precedent's first matching line is quoted with its `[SRC-…]` citation and exact offsets. Each quote says which record cites that source: the decision, the outcome, or both (a source cited by both can hold planning lines as well as results). For teams, lines are taken in document order. For everything else, lines from what happened (the outcome's sources) come before what was planned, and content lines come before headers such as "Reporter:" or "Slide 2:".
- An item is **Assumed for demo** when a precedent is linked to it in the memory graph but none of its sources mention it. The rationale says so.
- **Confidence** counts the precedents with a quoted line: 3 or more high, 2 medium, 1 low. Assumptions are always low. It describes the evidence; it never confirms or approves anything.
- A system, customer group or risk quoted from the outcome of a failed or partial precedent carries a warning. For the demo question that flags high-volume contract customers and competitive response in Germany (from the failed German 6% rollout, DEC-001), billing errors (Italy) and short notice (UK and Ireland).
- **Cost and effort** are always assumptions: the sum of per-system ranges in `system_estimates` over the affected systems, with the formula shown and amounts to three decimals, exactly as applied. For the demo question they come to $0.20M–$0.35M and 7–12 person-weeks.
- With no visible precedent, or nothing a rule recognises, the tab shows "Insufficient verifiable evidence." Precedent IDs the role cannot see are ignored exactly like unknown ones. If a quoted line cannot be verified for the role, the whole assessment is withheld with the same message and the rest of the page keeps working.

## Scenario simulation (Agent 4)

Tab **4 · Scenario Simulation** compares the proposal's options (+3 / +5 / +7 %, plus the asked rate if it is another one) with two formulas, shown on the tab:

```
revenue_change = baseline × rate × (1 − exempt_share) − baseline × churn_loss_fraction
margin_change  = revenue_change × contribution_margin − implementation_cost
```

- Every input is editable: baseline revenue (USD M per quarter), exempt share, contribution margin, implementation cost, a churn loss per option, and the churn guardrail. Each edit recomputes every figure; **Restore default inputs** puts the defaults back. Percentages are kept to three decimals and money to two: a value typed with more is rounded to what its box shows, and that is the value applied. No headline figure is stored anywhere.
- With the defaults the options come to +$8.4M / +$14.0M / +$18.8M revenue and +$5.58M / +$9.50M / +$12.86M margin a quarter. For +5%: $400M × 5% × 90% = $18M, minus $400M × 1% = $4M, gives $14M; × 70% − $0.3M gives $9.5M.
- Each option shows its formula with the numbers filled in, a revenue retention proxy (1 − churn loss fraction, labelled as a proxy, not customer retention), SLA "unchanged / not modeled", and whether its churn loss is within the guardrail. The guardrail is only compared here; it changes no figure. Agent 5 applies it.
- **Where the inputs come from** labels each one. Four defaults are stated in fixture sources and cite them: exempt share 10% ("roughly a tenth", SRC-022), churn guardrail 1.2% (SRC-014), churn loss 0.6% at +3% (SRC-004, SRC-015) and 1.0% at +5% (SRC-006, SRC-011). Baseline revenue, contribution margin, implementation cost and the 1.6% churn loss at +7% are assumed for demo; the +7% note shows Iberia's planned and actual churn as context, not support. An edited value becomes an assumption and drops its citation. An asked rate without a default (say +4%) gets a churn loss read off the straight lines between the defaults, labelled as such.
- The tab notes when the exempt share and the draft proposal in tab 3 disagree: a non-zero exempt share while the proposal does not exclude contract customers, or a 0% exempt share while it does.
- Results are computed for display and are not stored.

## Recommendation (Agent 5)

Tab **5 · Recommendation** turns the precedents (tab 2), the impact analysis (tab 3) and the scenarios (tab 4) into one advisory card. It never executes, sends or stores anything; a named Pricing Director must approve.

- **Rules** (`DEFAULT_RULES` in `src/decision_memory/agents/recommendation_agent.py`). Each option must pass all three:
  1. its churn loss is within the churn guardrail from tab 4;
  2. at least one visible precedent within ±0.5 points of its rate was a **success**;
  3. its revenue change is positive.

  The eligible option with the highest revenue change is recommended (the lower rate on a tie). Above +3% it is a **phased pilot**, otherwise it may proceed.
- **Cautions, not support.** A partial or failed precedent near a rate never backs it. It is shown as a caution and raises the risk level.
- **Abstaining.** With no visible precedent, or no option backed by one, the tab says "Insufficient evidence to recommend." If options are backed but each one exceeds the guardrail or has no upside, it says "Do not proceed".
- **Demo question:** phased pilot at +5%, risk medium. France (DEC-004) is the successful precedent; the UK and Italy 5% surcharges (both partial) are cautions. +7% is excluded because its 1.6% churn loss exceeds the 1.2% guardrail, and none of the precedents found for the question is a success near that rate. +3% is eligible but has the lower uplift (+$8.40M against +$14.00M).
- **Every reason cites a source the role can see:** the precedent and the cautions, the guardrail (SRC-014), the phased-pilot policy (SRC-014), the contract-customer exclusion policy (SRC-022), and the key risks. Key risks are impact risks quoted from a failed precedent with at least medium confidence: for the demo, churn among high-volume contract customers and competitive response in Germany. Each key risk cites only the lines from the failed precedent it names.
- **Assumptions are listed apart from reasons:** the computed figures, every input that is assumed for demo, and demo settings such as the two-market pilot scope and the ±0.5-point evidence rule. An edited guardrail stops being a cited reason and appears here. A policy whose source the role cannot see is applied as a labelled demo rule, not cited.
- **Condition.** The recommendation holds only if high-volume contract customers are excluded. The card says whether the draft proposal in tab 3 already excludes them.
- **Risk level** is rule-based: high if a precedent near the chosen rate failed; medium if one was partial, a key risk exists or there is no impact analysis; low otherwise.
- The **action plan** is for the approver and ends with the approval requirement. For a pilot it tracks the churn loss share, which does not depend on scale; the scenario revenue figures cover the whole baseline, so the plan says to scale them to the pilot markets and lists that as an assumption. **How each option was judged** lists every option with its verdict, successful precedents and cautions.
- The recommendation is checked with `verify_recommendation` for the active role before it is shown. If a citation cannot be verified, nothing from it is shown. It is stored only when someone approves or rejects it in tab 6.

## Governance (Agent 6)

Tab **6 · Governance** closes the loop: a human decides on the tab 5 recommendation, the pilot is tracked for 60 days, and its outcome goes back into memory. Everything here is simulated. Approvals, KPI values and outcomes are records typed into the demo. Nothing changes a price, sends a message or opens a ticket.

- **Who can do what.** Pricing Director and Restricted Executive can approve, reject and close. The Analyst can record weekly KPIs but cannot approve, reject or close. Role switching is a demo control, not authentication. Every action asks for a (fictional) name for the audit trail.
- **Approving** needs a recommendation that names a rate (phased pilot or proceed) and its condition met: high-volume contract customers excluded in tab 3. Otherwise the tab says why, and Approve is not offered or stays disabled. For a pilot the approver picks the 2 pilot markets (European markets from visible decisions) and the pilot markets' share of baseline revenue (20% by default, assumed for demo; the same default applies when none is given). Only one pilot per question can be open at a time.
- **What an approval stores**, in one transaction: the proposal (`draft → approved`), the recommendation, the approval, a generated **approval record** (a `governance_record` source, `SRC-023` in the demo), the pilot **decision** (`DEC-017`) citing that record, its links (`INFORMED_BY` the precedents used, `APPLIES_TO` the pilot markets), 4 milestones and 8 weekly KPI slots.
- **The weekly plan** is a visible formula: planned weekly revenue change = quarterly revenue change × pilot share ÷ 13 weeks. For the demo, +$14.00M × 20% ÷ 13 = +$0.215M a week. Planned churn loss is the option's 1.0% of baseline revenue, with a pause-and-review flag above the 1.2% guardrail in force at approval (stored on the proposal).
- **Milestones**: week 1 recorded (day 7), week 4 checkpoint (day 28), week 8 checkpoint (day 56), 60-day executive review (day 60, per the pilot policy in SRC-014). Days count from the approval date. A milestone is done when its week is recorded or the pilot closes. It is at risk if a recorded week up to it is flagged or it is overdue.
- **Weekly KPIs** are entered one week at a time, in order, and never overwritten. They are prefilled with the plan. A week is flagged if churn loss is above plan or above the guardrail, revenue change is below plan, or the SLA is at risk or breached. The first entry moves the pilot to `in_progress`; a pilot cannot start without one.
- **Closing** (the executive review, approver roles only) shows a rule-based verdict suggestion, and the verdict selector resets to it each time another week is recorded. **Failed:** average churn loss above the guardrail, or no revenue gain. **Success:** at or above plan on revenue and churn, with the SLA held. **Partial:** anything else. The SLA counts as held only in weeks where it was checked; if every week says "not modeled", the outcome says so. The reviewer decides, and a verdict that differs from the suggestion needs a note. Closing writes a generated **close report** (`SRC-024`) and an **Outcome** (`OUT-015`) citing its result and verdict lines. Figures are quarter-equivalents of the recorded weeks (weekly average × 13), for the pilot markets. The pilot moves to `completed`. A pilot can be closed after week 1 for the demo; the tab says how few weeks the outcome rests on.
- **Back in memory.** On the next rerun, tab 2's search and graph show the pilot decision with its outcome, and the next recommendation counts it: a successful +5% pilot backs +5%, a partial or failed one is a caution. The pilot ranks first for its own question, so the failed Germany precedent drops to sixth place. It is still returned as a warning, so the key risks stay on the card. Tab 3 never quotes the generated approval and close records: they restate the app's own output and are not independent evidence.
- **State machine**: `draft → approved → in_progress → completed`, or `draft → rejected`. Any other move is refused with an error and leaves no audit event. Rejecting stores the proposal, recommendation and rejection, but no decision or tracker.
- **Audit trail.** Every change writes an audit event: approval, decision capture and confirmation, each status change, each KPI entry, each milestone state change, and the outcome. The tab lists the trail for the selected record. Details stay neutral: IDs and states, never question text or notes.
- **Visibility.** The generated records get the same audience as the recommendation: the roles that can see every excerpt it cites. A pilot approved on restricted evidence is invisible to other roles, everywhere, like a record that does not exist.

## Demo roles and evidence

Pick a **Demo role** in the sidebar: Pricing Director, Analyst or Restricted Executive. The **Evidence explorer** below the stage tabs lists only the sources that role may see, and opens any of them in full. A source can be linked directly, for example `http://localhost:8501/?role=analyst&evidence=SRC-006`.

How visibility works:

- Each source lists the roles allowed to see it.
- A decision is shown only if it is confirmed and **every** source it cites is visible to the role. One hidden source withholds the whole decision, along with its outcomes, links and any entity reachable only through it.
- A hidden record behaves exactly like one that does not exist: no title, snippet, ID or count is shown.
- A statement that cannot be backed by visible sources is replaced with "Insufficient verifiable evidence." Nothing is filled in.
- Citations look like `[SRC-006]` and carry the source title, type, date and the exact excerpt.
- Capturing, confirming, editing, approving or rejecting, recording a weekly KPI, changing a milestone and closing a pilot each write an audit event.

In the seeded data, `SRC-019` (and so decision `DEC-015`) is visible to the Restricted Executive only, and `SRC-021` (and so `DEC-016`) is hidden from the Analyst.

## Security limitations

**Role switching is for demonstration only. It is not authentication or access control.**

- Anyone who can open the app can pick any role, or put `?role=restricted_exec` in the address. There are no user accounts, passwords or sessions.
- The filter shows what a correctly implemented permission model would hide. It does not protect the data: the SQLite file and the fixture files hold every record in plain text for anyone with access to this folder.
- "Sources" are fictional text files. The `mock://` references are labels, not links, and nothing connects to Teams, Jira, SharePoint, email or ServiceNow.
- Source text is treated as untrusted data: it is shown literally, never rendered as Markdown or HTML, and never interpreted as an instruction. In the graph, labels are HTML-escaped before Plotly sees them. That is tested for the display and filtering paths that exist today; the optional LLM path (Stage 12) will need its own checks.
- An "approval" is a stored record. No pricing change, message, ticket or payment is ever executed.

A real deployment would need enterprise sign-in, permissions inherited from each source system at query time, encryption, and an independent security review.

## Test

```bash
source .venv/bin/activate
python -m pytest
```

Tests use temporary databases and never touch `data/demo.db`.

Linting is optional. Ruff is not in the requirements; install it into the virtual environment if you want it. Settings are in [ruff.toml](ruff.toml).

```bash
pip install ruff
ruff check .
```

## Configuration (all optional)

Copy `.env.example` to `.env` to change defaults. `.env` is git-ignored; never commit a key.

| Variable | Default | Meaning |
|----------|---------|---------|
| `LLM_MODE` | `offline` | `anthropic` only takes effect when a key is also set (Stage 12; not built yet) |
| `ANTHROPIC_API_KEY` | unset | Optional. Read from the environment only |
| `DM_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `DM_DB_PATH` | `data/demo.db` | SQLite file, relative to the repo root |

## Layout

```
app.py                      Streamlit entry point (UI wiring only)
src/decision_memory/
  config.py                 typed settings
  deps.py                   missing-package check (standard library only)
  logging_setup.py
  models.py                 Pydantic contracts for every stored record
  db.py                     SQLite connection and create-table bootstrap
  repository.py             typed create/read/update; the only module that runs SQL
  seed.py                   fixture loader, idempotent seed, reset
  authz.py                  demo roles and MemoryView, the role-scoped read window
  provenance.py             [SRC-...] citations, evidence items, insufficient-evidence rule
  audit.py                  audited capture, review, approval and pilot-tracking writes
  agents/capture_agent.py   Agent 1: transcript parser and confirm-and-save
  agents/memory_agent.py    Agent 2: role-filtered decision graph (NetworkX projection)
  agents/impact_agent.py    Agent 3: impact rules, quoted evidence, cost and effort ranges
  agents/scenario_agent.py  Agent 4: pure scenario formulas, defaults and their sources
  agents/recommendation_agent.py  Agent 5: option rules, cited reasons, action plan
  agents/governance_agent.py      Agent 6: approval, 60-day tracker, KPIs, outcome write-back
  ui/shell.py               banners, sidebar with role selector and guide switch, six stage tabs
  ui/guide.py               guided demo panel: steps, navigation, live checks
  demo_guide.py             guided demo steps, live checks and talk tracks (pure)
  ui/capture.py             Agent 1 view: transcript, card, review, confirm
  precedent_search.py       transparent precedent ranking with abstention
  ui/search.py              Agent 2 view: question, filters, cited precedents
  ui/memory_graph.py        Agent 2 view: Plotly network with a table fallback
  ui/impact.py              Agent 3 view: draft proposal, labelled impact items, rules
  ui/scenarios.py           Agent 4 view: editable inputs, formulas, per-option results
  ui/recommendation.py      Agent 5 view: advisory card, condition, option verdicts
  ui/governance.py          Agent 6 view: approval, tracker, KPI entry, review, audit trail
  ui/evidence.py            evidence explorer and source detail view
scripts/seed_demo.py        load the baseline
scripts/reset_demo.py       wipe and rebuild the baseline
data/fixtures/              synthetic fixtures (committed)
tests/                      pytest suite (offline, no API key)
docs/                       product spec and implementation plan
```

## Troubleshooting

- **"Missing Python packages: …"** — the virtual environment is not active or not installed. Run the set-up commands above.
- **`streamlit: command not found`** — run `source .venv/bin/activate` first.
- **Port 8501 in use** — `streamlit run app.py --server.port 8502`.
- **"Reset failed: … was not created by this app"** — `DM_DB_PATH` or `--db` points at some other file. Point it at the demo database.

## Not available yet

- **Demo reset button**: the sidebar button is still a disabled placeholder; use `python scripts/reset_demo.py`.
- The optional Anthropic mode (Stage 12), the demo script and the in-app reset button (Stage 14).
- The guide does not check off the Hook, and its live checks describe the app's state, not what the presenter has shown: a decision captured in an earlier run already counts for step 2. Reset the demo database before a rehearsal. Step 6 counts only a pilot for the question on screen.
- Governance tracks against the plan fixed at approval. Milestone states are recalculated only when someone records a KPI or closes the pilot, so an overdue milestone shows as at risk only after the next entry. Tracker days count from the approval date, not from the proposal's effective quarter.
- The open-pilot check compares question text, so the same proposal phrased differently can be approved twice.
- The question text is copied into the approval record. A question typed by a role with wider access is visible to whoever can see that record. Only cited sources set the audience, not what was typed.
- Record IDs are sequential, so a gap (for example `DEC-017` hidden from a role while `DEC-018` is visible) shows that a hidden record exists. The seeded baseline already has such a gap (`DEC-015`).
- The recommendation uses only the precedents search returned for the question, and its verdicts say so ("among those found for this question"). A relevant decision outside that list does not count: the failed 7% surcharge in Iberia does not appear as a caution for the demo question, and a question asked at 4% does not find France's successful 5%, so +5% is excluded there and +4% is recommended.
- Impact rules match words in source lines. A source that describes an effect in words a rule does not list is missed, and a line that mentions a term only in passing (for example "No competitive response was observed") is still quoted as evidence. Read the quoted lines; the confidence label counts precedents, it does not judge them.
- The rule table and per-system cost ranges are edited in code (`impact_agent.py`), not in the app.
- Precedent search matches words, not meaning. The equivalence list covers common pricing wording, but a question phrased in words it does not list can still miss or abstain.
- Capture reviews one decision per transcript. If a transcript has several `DECISION:` lines, the first is reviewed.
