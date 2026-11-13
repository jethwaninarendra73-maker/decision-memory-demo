# Product Spec — Enterprise Decision Intelligence & Institutional Memory Agent (Hackathon MVP)

## 1. Goal

A Pricing Director asks: **"Should we increase the freight surcharge by 5% for European customers next quarter?"**
In five minutes the app shows that the company already holds the answer: it captures a decision from a meeting, retrieves cited precedents, assesses impact, compares +3 / +5 / +7 %, recommends a guarded option, records human approval of a pilot, and writes KPI outcomes back into memory.

Everything runs locally and offline on fictional data. Outputs are illustrations from editable assumptions, not forecasts or authorised decisions.

## 2. Users and roles (demo only, selected in the UI)

| Role | Sees | Can |
|------|------|-----|
| `pricing_director` | Sources tagged `pricing_director` | Ask, review, approve/reject pilots |
| `analyst` | Sources tagged `analyst` | Ask, capture and edit drafts; cannot approve |
| `restricted_exec` | All sources incl. `restricted_exec` | Everything; used to prove restricted records stay hidden from others |

Role switching is a demonstration control, not authentication.

## 3. Entities

Implemented in `src/decision_memory/models.py` (Stage 02). Every stored record has a stable prefixed ID, `created_at`, `updated_at` and `synthetic: true`. Money is USD millions per quarter (`*_musd`); rates and shares are fractions (0.05 = 5%).

| Entity | ID prefix | Key fields |
|--------|-----------|------------|
| `SourceDocument` | `SRC-` | source_type (`teams_transcript` · `steering_deck` · `jira_ticket` · `email` · `servicenow`), title, document_date, author_label, body, `access_roles[]`, mock_link, region |
| `SourceRef` | — | source_id, excerpt, start_offset, end_offset; always an exact slice of the source body |
| `Decision` | `DEC-` | title, statement, decision_date, owner, stakeholders[], region, market, customer_segment, surcharge_rate, rationale, alternatives[], risks[], expected_outcome, status (`draft` · `confirmed`), source_refs[]; a confirmed decision must cite at least one excerpt |
| `Entity` | `ENT-` | kind (`market` · `segment` · `customer_group` · `system` · `risk` · `initiative` · `stakeholder`), name, description |
| `Relation` | `REL-` | from_id, to_id, relation_type (`APPLIES_TO` · `AFFECTS` · `HAS_RISK` · `OWNED_BY` · `INVOLVES` · `PART_OF` · `SIMILAR_TO` · `CONTRADICTS` · `SUPERSEDES` · `INFORMED_BY`), note. `SUPPORTED_BY` (decision or outcome → each cited source) and `RESULTED_IN` (decision → outcome) edges are derived from source refs and outcomes, not stored |
| `Outcome` | `OUT-` | decision_id, verdict (`success` · `partial` · `failed`), observed_on, expected vs actual revenue change, expected vs actual churn loss fraction, sla_status, explanation, source_refs[] |
| `Proposal` | `PRO-` | question, region, target_segments[], effective_quarter (`2027-Q1`), surcharge_options[], exclusions[], status, requested_by_role, decision_id |
| `ScenarioResult` | `SCN-` | proposal_id, surcharge_rate, assumptions (baseline revenue, exempt share, churn loss fraction, contribution margin, implementation cost), gross_uplift, churn_loss, revenue_change, margin_change, formula_trace |
| `Recommendation` | `REC-` | proposal_id, decision_type (`phased_pilot` · `proceed` · `do_not_proceed` · `insufficient_evidence`), recommended_rate, reasons[] (each `source_supported` with source_refs, or labelled `assumed_for_demo`), excluded_options[], risk_level, assumptions[], uncertainty, action_plan[], approval_required_by |
| `Approval` | `APR-` | recommendation_id, approver_label, role, verdict (`approved` · `rejected`), chosen_rate, reasons, decided_at |
| `Milestone` | `MIL-` | proposal_id, name, due_date, state (`planned` · `in_progress` · `done` · `at_risk`) |
| `KPIUpdate` | `KPI-` | proposal_id, week (1–8, one per week), planned vs actual revenue uplift, planned vs actual churn loss fraction, sla_status, risk_flag, note |
| `AuditEvent` | `AUD-` | at, actor_label, role, action (`seed` · `capture` · `confirm` · `edit` · `approve` · `reject` · `kpi_update` · `close`), entity_type, entity_id, detail |

`DecisionDraft` (built in Stage 04) is what Agent 1 proposes: the decision fields, a rule-based confidence per field, `flags[]` for anything missing or uncertain, and `evidence[]` with the exact transcript offsets of each value. It is never stored; a reviewer signs off a `ReviewedDecision`, which is saved as a confirmed `Decision`. `ImpactAssessment` (Stage 07) is added with its agent.

Proposal state machine: `draft → approved → in_progress → completed`, or `draft → rejected`. Any other transition is refused (enforced in Stage 10).

### Seeded baseline (all fictional)

| Table | Rows | Notes |
|-------|------|-------|
| Sources | 22 | 7 Teams-style transcripts, 9 steering deck excerpts, 6 Jira-style tickets |
| Decisions | 16 | 14 European across 8 markets, 1 North America, 1 Asia-Pacific |
| Outcomes | 14 | 7 success, 4 partial, 3 failed; 2 decisions have no outcome yet |
| Entities | 47 | 11 markets, 5 segments, 1 customer group, 4 systems, 9 risks, 3 initiatives, 14 stakeholders |
| Relations | 147 | incl. 2 `SUPERSEDES`, 2 `CONTRADICTS`, 4 `SIMILAR_TO`, 4 `INFORMED_BY` between decisions |

Role tags: `SRC-019` is `restricted_exec` only (so `DEC-015` is hidden from the other roles); `SRC-021` is hidden from `analyst` (so `DEC-016` is too). All other sources are visible to all three roles.

## 4. Six-agent sequence

```
Sources ─▶ 1 Capture ─▶ 2 Memory graph ◀─────────────────────────┐
                           │ (permission-filtered)              │ outcomes
Question ─▶ precedent search ─▶ 3 Impact ─▶ 4 Scenarios ─▶ 5 Recommendation ─▶ human approval ─▶ 6 Governance
```

| # | Agent | Input | Output | Rule that makes it trustworthy |
|---|-------|-------|--------|--------------------------------|
| 1 | Decision Capture | Synthetic transcript (`.txt`/`.md`, pasted or fixture) | `DecisionDraft` with offsets | Deterministic parser; flags missing fields; distractor talk is not a decision; only human-confirmed drafts enter memory |
| 2 | Institutional Memory | Confirmed decisions, sources, outcomes (SQLite) | NetworkX projection + Plotly view | Projection, not a second store; built only from records the role can see |
| 3 | Impact Analysis | `Proposal` + accessible precedent IDs | `ImpactAssessment` | Rules from editable config; every item `source_supported` or `assumed_for_demo`; cost as illustrative range |
| 4 | Scenario Simulation | `ScenarioAssumptions`, options 3/5/7 % | `ScenarioResult` ×3 | Pure formulas, shown on screen, recomputed on edit |
| 5 | Recommendation | Precedents + impact + scenarios + guardrails | `Recommendation` | Guardrail rules, cited reasons, abstains without evidence, advisory only |
| 6 | Governance | Approved recommendation, KPI entries | Tracker, `Outcome` linked to decision | Simulated approval; valid transitions; outcome flows back to graph and search |

Precedent search (between 2 and 3) ranks accessible decisions by lexical overlap + region/segment/rate match + outcome relevance + recency decay, shows each score component, returns the top 3–5, and abstains when no decision is both topical (minimum lexical overlap) and above the score threshold. Failed precedents are surfaced as warnings, never as templates to repeat. Implemented in `src/decision_memory/precedent_search.py` (Stage 06).

### Scenario formulas (defaults are synthetic)

```
revenue_change = baseline × surcharge × (1 − exempt_share) − baseline × churn_loss_fraction
margin_change  = revenue_change × contribution_margin − implementation_cost
```

Defaults: baseline $400M/quarter, exempt_share 10%, churn_loss_fraction 0.6% / 1.0% / 1.6% for +3 / +5 / +7 %, contribution_margin 70%, implementation_cost $0.3M.

| Option | Revenue change | Margin change | Churn loss vs 1.2% guardrail |
|--------|---------------|---------------|-------------------------------|
| +3% | +$8.4M | +$5.58M | 0.6% — passes |
| +5% | +$14.0M | +$9.50M | 1.0% — passes |
| +7% | +$18.8M | +$12.86M | 1.6% — **fails** |

Recommendation rule: drop options that breach the churn guardrail (≤ 1.2% of baseline) or lack adequate evidence; require explicit exclusion of high-volume contract customers; among remaining options pick the highest revenue upside. Default result: **phased +5% pilot** in two markets, weekly revenue/churn KPI checks, executive review after 60 days, named human approver required.

## 5. Five-minute demo outline

| Time | Step | On screen | Agent |
|------|------|-----------|-------|
| 0:00–0:30 | Hook | Three scattered synthetic artifacts: Teams transcript, steering deck excerpt, Jira ticket | — |
| 0:30–1:15 | Capture | Transcript → extracted decision card → review → confirm → saved | 1 |
| 1:15–2:15 | Ask | Director types the surcharge question; graph highlights cited European and non-European precedents, incl. a failed one | 2 + search |
| 2:15–3:30 | Analyze | Impact checklist with source/assumption badges, then +3/+5/+7 columns with formulas | 3, 4 |
| 3:30–4:30 | Recommend | Card: phased +5% pilot, +7% excluded by guardrail, key risks (high-volume contract customers, competitive response in Germany), cited reasons | 5 |
| 4:30–5:00 | Close the loop | Approve pilot, 60-day tracker, enter week-1 KPI, outcome visible in graph/search | 6 |

Talk track for "where does +$14M come from?": $400M × 5% × 90% non-exempt = $18M, minus $400M × 1% churn loss = $4M, gives $14M revenue before margin treatment. Every input is editable on screen.

## 6. Acceptance criteria

**Baseline (offline, no API key, single command)**
- [ ] `streamlit run app.py` starts with no network and no `ANTHROPIC_API_KEY`.
- [ ] Seed loads ≥ 12 fictional historical freight pricing decisions (mostly European, 1–2 non-European), ≥ 3 source types, a mix of successful, partial and failed outcomes, and at least one contradictory or poor precedent.
- [ ] `seed_demo.py` is idempotent; `reset_demo.py` restores byte-identical baseline query results.
- [ ] Every page with numbers or advice shows the synthetic-data banner.

**Capture (Agent 1)**
- [ ] The demo transcript yields one decision with owner, date, rationale, risk and exact evidence offsets; distractor discussion produces no decision.
- [ ] Missing or uncertain fields are flagged, not guessed.
- [ ] Only confirmed drafts are stored; confirmation writes an `AuditEvent`.

**Memory and retrieval (Agent 2 + search)**
- [ ] Graph has typed edges and no dangling references; it is rebuilt from SQLite.
- [ ] The Europe surcharge question returns the intended top precedents with score components and `[SRC-…]` citations.
- [ ] Unrelated regions rank lower; an unknown topic returns "insufficient verifiable evidence".

**Permissions**
- [ ] Filtering happens before retrieval and recommendation assembly.
- [ ] For an unauthorised role, a `restricted_exec` record never appears in search results, graph nodes/labels/edges, citations, impact items or recommendation reasons.

**Impact (Agent 3)**
- [ ] Lists affected teams, systems, customer groups, risks, cost range and effort, each marked `source_supported` or `assumed_for_demo` with confidence and rationale.
- [ ] Flags high-volume contract customers and competitive response in Germany from retrieved precedents.

**Scenarios (Agent 4)**
- [ ] Defaults compute +$8.4M / +$14.0M / +$18.8M revenue and +$5.58M / +$9.50M / +$12.86M margin.
- [ ] Editing any assumption recomputes instantly; formulas and inputs are shown; no headline number is hard-coded.
- [ ] Retention is shown as a labelled proxy; SLA shows "unchanged / not modeled".

**Recommendation (Agent 5)**
- [ ] Default output is a phased +5% pilot; +7% is excluded with the guardrail reason; +3% is noted as lower uplift.
- [ ] Every reason cites a visible source; missing evidence produces "insufficient evidence to recommend".
- [ ] Output is advisory and names the human approval requirement.

**Governance (Agent 6)**
- [ ] `analyst` cannot approve; `pricing_director` can (simulated, labelled as such).
- [ ] Approval creates a 60-day tracker with milestones and 8 weekly KPI slots.
- [ ] Entering a KPI update and closing the pilot attaches an `Outcome` to the decision, visible in graph and precedent search after refresh.
- [ ] Invalid state transitions are refused; every change is audited.

**Safety**
- [ ] No outbound calls, emails, tickets, price changes or payments anywhere in the code.
- [ ] Instructions embedded in source documents are treated as text and have no effect.
- [ ] No secrets in the repo; `.env`, `.venv`, `data/*.db` are git-ignored.
- [ ] Optional Anthropic mode (if built) falls back to offline on missing key, timeout or validation failure; tests use mocks only.
