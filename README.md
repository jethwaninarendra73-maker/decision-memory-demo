# Enterprise Decision Intelligence & Institutional Memory Agent — Hackathon MVP

> **SYNTHETIC DEMO — NOT A REAL FORECAST.** All data is fictional. Nothing here connects to Teams, Jira, email or any company system, and nothing executes a business action.

An offline-first Streamlit app that walks a Pricing Director through one question, *"Should we increase the freight surcharge by 5% for European customers next quarter?"*, using six logical agents: Decision Capture, Institutional Memory, Impact Analysis, Scenario Simulation, Recommendation and Governance.

**Build status: Stage 02 of 15.** The app shell runs with six labelled stage tabs, and the storage layer exists: typed models, a SQLite schema, a repository, and a repeatable synthetic baseline. The agents are still stubs and the UI does not read the data yet. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

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
  agents/                   six agent modules (stubs until their stage)
  ui/shell.py               banner, sidebar, six stage tabs
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
- **Permissions**: role tags are stored on every source, but nothing enforces them until Stage 03.
- All six agents, the data views in the UI, and the optional Anthropic mode.
