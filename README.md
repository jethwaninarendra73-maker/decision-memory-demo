# Enterprise Decision Intelligence & Institutional Memory Agent — Hackathon MVP

> **SYNTHETIC DEMO — NOT A REAL FORECAST.** All data is fictional. Nothing here connects to Teams, Jira, email or any company system, and nothing executes a business action.

An offline-first Streamlit app that walks a Pricing Director through one question, *"Should we increase the freight surcharge by 5% for European customers next quarter?"*, using six logical agents: Decision Capture, Institutional Memory, Impact Analysis, Scenario Simulation, Recommendation and Governance.

**Build status: Stage 06 of 15.** Agent 1 (Decision Capture) turns a synthetic transcript into a reviewed, confirmed decision in memory. Agent 2 (Institutional Memory) answers a pricing question with ranked, cited precedents and shows memory as a typed decision graph, both filtered for the selected demo role. Underneath are typed models, a SQLite store with a repeatable synthetic baseline, permission filtering, citations and an audit trail. Agents 3 to 6 are still stubs. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

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

## Demo data: seed and reset

```bash
python scripts/seed_demo.py     # load the synthetic baseline; safe to run repeatedly
python scripts/reset_demo.py    # delete data/demo.db and rebuild the baseline
```

- **Seed** upserts the fixture records by ID. Running it twice changes nothing, and it leaves records you created in the app alone.
- **Reset** removes everything created in the app. It refuses to delete a file this app did not create.
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
- Up to five decisions are returned. A decision must share enough of the question's terms (lexical at least 0.5) and score at least 0.45, so a matching region or rate alone never makes something a precedent. If nothing qualifies, the answer is "Insufficient verifiable evidence." and nothing is returned.
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

## Demo roles and evidence

Pick a **Demo role** in the sidebar: Pricing Director, Analyst or Restricted Executive. The **Evidence explorer** below the stage tabs lists only the sources that role may see, and opens any of them in full. A source can be linked directly, for example `http://localhost:8501/?role=analyst&evidence=SRC-006`.

How visibility works:

- Each source lists the roles allowed to see it.
- A decision is shown only if it is confirmed and **every** source it cites is visible to the role. One hidden source withholds the whole decision, along with its outcomes, links and any entity reachable only through it.
- A hidden record behaves exactly like one that does not exist: no title, snippet, ID or count is shown.
- A statement that cannot be backed by visible sources is replaced with "Insufficient verifiable evidence." Nothing is filled in.
- Citations look like `[SRC-006]` and carry the source title, type, date and the exact excerpt.
- Capturing, confirming, editing and approving each write an audit event.

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
  audit.py                  audited capture, review and approval writes
  agents/capture_agent.py   Agent 1: transcript parser and confirm-and-save
  agents/memory_agent.py    Agent 2: role-filtered decision graph (NetworkX projection)
  agents/                   agents 3 to 6 (stubs until their stage)
  ui/shell.py               banner, sidebar with role selector, six stage tabs
  ui/capture.py             Agent 1 view: transcript, card, review, confirm
  precedent_search.py       transparent precedent ranking with abstention
  ui/search.py              Agent 2 view: question, filters, cited precedents
  ui/memory_graph.py        Agent 2 view: Plotly network with a table fallback
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
- Agents 3 to 6 and the optional Anthropic mode.
- Precedent search matches words, not meaning. The equivalence list covers common pricing wording, but a question phrased in words it does not list can still miss or abstain.
- Capture reviews one decision per transcript. If a transcript has several `DECISION:` lines, the first is reviewed.
