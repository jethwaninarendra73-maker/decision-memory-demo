# Enterprise Decision Intelligence & Institutional Memory Agent — Hackathon MVP

> **SYNTHETIC DEMO — NOT A REAL FORECAST.** All data is fictional. Nothing here connects to Teams, Jira, email or any company system, and nothing executes a business action.

An offline-first Streamlit app that walks a Pricing Director through one question, *"Should we increase the freight surcharge by 5% for European customers next quarter?"*, using six logical agents: Decision Capture, Institutional Memory, Impact Analysis, Scenario Simulation, Recommendation and Governance.

**Build status: Stage 03 of 15.** The app shell runs with six labelled stage tabs, a demo role selector and an evidence explorer. Underneath are typed models, a SQLite store with a repeatable synthetic baseline, permission filtering, citation utilities and an audit trail. The six agents are still stubs. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

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
- Source text is treated as untrusted data: it is shown literally, never rendered as Markdown or HTML, and never interpreted as an instruction. That is tested for the display and filtering paths that exist today; the optional LLM path (Stage 12) will need its own checks.
- An "approval" is a stored record. No pricing change, message, ticket or payment is ever executed.

A real deployment would need enterprise sign-in, permissions inherited from each source system at query time, encryption, and an independent security review.

## Test

```bash
source .venv/bin/activate
python -m pytest
```

Tests use temporary databases and never touch `data/demo.db`.

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
  agents/                   six agent modules (stubs until their stage)
  ui/shell.py               banner, sidebar with role selector, six stage tabs
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
- All six agents, the decision graph, precedent search, and the optional Anthropic mode.
