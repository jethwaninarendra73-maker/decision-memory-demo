# Handoff

**SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.**

For whoever picks this up after the hackathon: what exists, how to change it safely, and what is left.

## What this is

A local, offline Streamlit app that answers one question with six cooperating agents: *"Should we increase the freight surcharge by 5% for European customers next quarter?"* It captures decisions from meeting transcripts, finds cited precedents, assesses impact, compares +3/+5/+7% with visible formulas, recommends a guarded option, and records a simulated approval, a 60-day pilot and its outcome back into memory. Every company, person and number is fictional.

**Status (2026-11-17):** Stages 00–15 are complete. The release gate passes for a local demo: all 28 acceptance criteria and the six `CLAUDE.md` quality gates were re-verified on that date ([RELEASE_SCORECARD.md](RELEASE_SCORECARD.md)). It is a demonstrator, not a product: no real data, no real users, no real integrations.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/reset_demo.py        # build data/demo.db from the fixtures
streamlit run app.py                # http://localhost:8501
python -m pytest -q                 # about 1.5 to 2 minutes, offline, no key
```

Built and tested on Python 3.15.2 on macOS only. `requirements.txt` and `ruff.toml` target Python 3.11 or later, but no older Python was tried. No network and no API key are needed, and the app only listens on `localhost` (`.streamlit/config.toml`), so other machines on the network cannot reach it. The optional Anthropic mode is described in the README.

## Where things are

| Document | Use it for |
|----------|-----------|
| [../README.md](../README.md) | How every tab behaves, configuration, troubleshooting, known limitations |
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | Scope, entities, scenario formulas, recommendation rule, acceptance criteria |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | The 16 stages and their gates |
| [QA_REPORT.md](QA_REPORT.md) | Test guards, the criterion-to-test matrix, browser checks, every review finding and its fix |
| [RELEASE_SCORECARD.md](RELEASE_SCORECARD.md) | The Stage 15 gate: what was verified, and what was not |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md), [DEMO_CHECKLIST.md](DEMO_CHECKLIST.md) | Presenting the five-minute demo |
| [../CLAUDE.md](../CLAUDE.md) | The rules the code follows (scope, permissions, citations, no fabrication, secrets) |
| [../data/fixtures/README.md](../data/fixtures/README.md) | The synthetic data and the rules its loader enforces |

## How it works, in five rules

The design rests on five rules. A change that breaks one of them is a bug, even if the tests still pass.

1. **Read through `MemoryView`.** Every agent, search and UI view reads through `authz.MemoryView(repo, role)`, never the repository itself, so nothing a role may not see reaches an agent or a page. A hidden record looks exactly like a missing one, including in refusals: a capture is refused as a duplicate only if the role can see the earlier copy.
   - **How it filters.** Sources, decisions, outcomes, entities and relations are filtered in SQL (`Repository.find_visible`), so hidden rows never leave the database layer. Recommendations are checked citation by citation inside the view, because their citations sit in a JSON column. Proposals, approvals, milestones, KPI updates and audit events are then read unfiltered and kept only if they belong to a recommendation the role may see. Those hidden rows are read into the view but never leave it.
   - **Who uses the unfiltered `Repository`.** Seeding, tests, and the write paths, where it is needed to write and to keep data consistent: `audit.py`, `capture_agent.confirm_capture` and the governance write functions in `governance_agent.py`. A write path must not tell the caller anything about records the role cannot see (see the capture duplicate check).
2. **Cite or abstain.** Every fact, impact item and recommendation reason carries a `[SRC-…]` citation with the exact excerpt and its character offsets (`provenance.py`). Anything not source-backed is labelled `assumed_for_demo`. With no evidence, the answer is "insufficient verifiable evidence". `verify_recommendation` runs before a recommendation is stored or shown.
3. **People confirm, the app records.** A captured decision enters memory only when a person confirms it (`capture_agent.confirm_capture`). Approvals, KPIs and outcomes are typed-in records written through `audit.py`, each with an audit event. Nothing prices, sends or opens anything.
4. **Source text is data.** It is shown with `st.text` or escaped Markdown, never as HTML, and never followed as an instruction. The optional model sees reason texts and source IDs only, never source bodies.
5. **Every number has a formula.** Scenario figures are computed from editable inputs (`scenario_agent.py`). No headline number is stored or typed into code; the tests change the inputs and check that the figures follow.

Agents are independent: each takes typed inputs and returns typed outputs (Pydantic contracts in `models.py`). Only `app.py` and `ui/` wire them together, and `ui/` holds no business logic.

**The answer at a glance** at the top of the page is built by the pure `glance.py` from that run's search, scenarios and recommendation for the active role, and drawn by `ui/glance.py` (which also draws the option cards). Add nothing to it that is not computed by an agent.

**What each tab shows first** is decided in `ui/view.py`. In the simple view (the default) each tab opens with a plain heading, one line on what it is for and its main result; supporting proof goes inside `details("…")`, which is a collapsed **Show details** section in the simple view and drawn in place in the full view. When you add something to a tab, ask whether a first-time viewer needs it to follow the story; if not, put it in `details`.

## Changing it safely

| Change | Where | Then |
|--------|-------|------|
| Add or edit synthetic history | `data/fixtures/*.json` and `sources/SRC-*.md` (keep the synthetic marker; excerpts must match the source text exactly once) | `python scripts/reset_demo.py`; run `tests/test_seed.py` and `tests/test_precedent_search.py`. Changing the demo precedents moves the demo's expected IDs: update `DEMO_SCRIPT.md`, and `tests/test_demo_kit.py` will tell you where |
| Change the recommendation rule (guardrail, evidence window, pilot scope) | `RecommendationRules` / `DEFAULT_RULES` in `agents/recommendation_agent.py` | `tests/test_recommendation_agent.py`; the tab shows the rules in use |
| Change impact rules or cost ranges | `ImpactRules` / `DEFAULT_RULES` and `system_estimates` in `agents/impact_agent.py` | `tests/test_impact_agent.py` |
| Change scenario defaults | `DEFAULT_INPUTS` in `agents/scenario_agent.py` | The default figures are an acceptance criterion: the spec, the demo script and several tests quote them |
| Change governance (pilot markets, share, verdict rules) | `GovernanceRules` in `agents/governance_agent.py` | `tests/test_governance_agent.py`, `tests/test_ui_governance.py` |
| Add search synonyms or stopwords | `SYNONYMS`, `STOPWORDS` in `precedent_search.py` | `tests/test_precedent_search.py` and `tests/search_eval.json` |
| Add a record type that can carry restricted content | `models.py`, `repository.py`, then a visibility rule in `authz.py` | A leak test: extend the hidden-string sweep in `tests/test_authz.py` |
| Add a demo role | `Role` in `models.py`, `ROLE_LABELS` in `authz.py`, `access_roles` in the fixtures | Tests parametrised with `list(Role)` pick the new role up automatically. The leak sweeps do not: they run only for the roles listed with a hidden-string list (`HIDDEN_FROM_DIRECTOR`, `HIDDEN_FROM_ANALYST` in `tests/conftest.py`, mapped by role in `tests/test_authz.py`, `tests/test_memory_agent.py` and `tests/test_precedent_search.py`, and the `[DIRECTOR, ANALYST]` parametrisations in the search, graph, governance and end-to-end tests). Write the list of strings the new role must never see, then add the role to those sweeps. Without that, a leak to the new role goes unnoticed |

Before committing: `python -m pytest -q` (it refuses to touch `data/demo.db` and blocks the network), and `ruff check .` if you have ruff. Fix bugs with the smallest change and a regression test; the QA report shows the pattern.

## Known limitations

The full list is in the README ("Not available yet") and the QA report ("Open items and limitations"). The ones most likely to matter next:

- **Search matches words, not meaning.** A precedent described in other words is missed, and the recommendation only weighs what search returned.
- **Role switching is not security.** Anyone can pick any role, and the SQLite file and fixtures hold everything in plain text.
- **Sequential IDs** can reveal that a hidden record exists (the seeded `DEC-015` gap).
- **Governance tracks against the plan fixed at approval;** milestone states update only on a KPI entry or close; the open-pilot check compares question text.
- **The Anthropic mode was tested against the live API once** (one call, brief accepted); everything else about it is tested with fake clients.
- **The demo reset is not audited** (the baseline must stay byte-identical) and any role can use it.

## Backlog

### Before the next presentation

- [ ] Rehearse aloud with a stopwatch, twice, with Wi-Fi off ([DEMO_CHECKLIST.md](DEMO_CHECKLIST.md)). The app's response time is measured and speech is estimated at 2 to 2.5 minutes (see the scorecard); only a person can time the whole thing.
- [ ] Record a narrated backup run. Until then, [demo_backup/slides.html](demo_backup/slides.html) is the fallback: still frames of one run, each under the synthetic-data label.
- [x] One live call in Anthropic mode was made on 2026-11-17 and passed (see the scorecard). Before presenting, set `LLM_MODE=offline` in `.env` again: the script assumes offline mode.

### Next improvements within the MVP's scope

- Semantic matching for precedent search, while keeping the score components visible and the abstain rule (this needs a dependency decision: `CLAUDE.md` rules out vector databases).
- Non-sequential or per-role record IDs, so a gap no longer hints at hidden records.
- Recalculate milestone states on every view, and detect duplicate pilots by proposal content rather than question text.
- An audit trail for demo resets kept outside the database (for example the log), if anyone needs to see when resets happened.
- Edit impact rules and cost ranges in the app instead of in code.
- Run `ruff format .` once, as its own commit, to clear the 22 files from early stages that the formatter would change.

### Out of scope for this MVP (needs a real project)

These were excluded on purpose (`CLAUDE.md`). Each one needs its own design, owner and security review.

| Item | What it would take |
|------|--------------------|
| Real integrations (Teams, SharePoint, email, Jira, ServiceNow, billing) | Connectors with read-only service accounts; permissions inherited from each source system at query time, not copied |
| Real company, customer or personal data | Data protection review, retention rules, encryption at rest, removal of the synthetic-data shortcuts |
| Authentication and access control | Enterprise sign-in and roles from the identity provider, replacing the demo role switch; the `MemoryView` filter is the place to enforce them |
| Executing decisions (prices, emails, tickets) | Kept out deliberately. If ever added: separate, audited, human-approved actions with their own controls |
| ML forecasting, vector or graph databases, cloud deployment | An architecture decision first; the current SQLite projection and rule-based agents are sized for a demo |

## Repository notes

- History: 14 commits up to Stage 14 (`228c230`), roughly one per stage; the first, `1a400a5`, holds the early scaffold, storage and seed work. Stage 15 adds this file and the scorecard.
- `git stash list` shows `stage-09 first attempt (superseded by redo)`. It is not needed; drop it when you are sure.
- `Enterprise Decision Intelligence & Institutional Memory Agent.docx` in the repo root is the original concept document. It is git-ignored and not part of the build.
- `data/demo.db` is generated and git-ignored; rebuild it at any time with `python scripts/reset_demo.py`.
