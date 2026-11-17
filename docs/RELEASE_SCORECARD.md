# Release scorecard

**SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION.**

Stage 15 release gate for the hackathon MVP, run on 2026-11-17 on macOS with Python 3.15.2, offline mode, no API key and without the optional `anthropic` package.

## Verdict

**The release gate passes for a local hackathon demo.** All 28 acceptance criteria in [PRODUCT_SPEC.md](PRODUCT_SPEC.md) §6 were checked again today and pass, and so do the six quality gates in `CLAUDE.md`. The release review found two problems, both fixed and covered by tests before this verdict (see Findings). The caveats under "Not verified" still apply. None of them blocks the demo, but two need a person: a timed rehearsal and a backup recording ([DEMO_CHECKLIST.md](DEMO_CHECKLIST.md)).

This is not a production release. The app is single-user and local, roles are a demo control, and every record is synthetic. See [HANDOFF.md](HANDOFF.md) for what a real deployment would need.

## How this was verified

1. **The cited tests, run on their own.** Every test that the acceptance matrix in [QA_REPORT.md](QA_REPORT.md) cites was collected and run on its own, per criterion: 77 test nodes, all passed (the one skip is below). None was missing. This used a one-off script that is not kept in the repository. Its result can be reproduced without it: the full suite runs every one of these tests, and `test_release_docs::test_every_test_a_release_document_cites_exists` fails if a cited test disappears.
2. **Spot check of what the evidence asserts.** I read five of the cited tests whose names were the least specific: impact labels, the proxy and SLA labels, region ranking, the SQL role filter and the tab banners. Each asserts what its criterion says.
3. **The full suite and the `CLAUDE.md` gates** (below), plus the real scripts and the running app on scratch databases.

`data/demo.db` was fingerprinted before and after: `763c2ff4…` both times. No check wrote to it.

## Quality gates (`CLAUDE.md`)

| Gate | Command or check | Result today |
|------|------------------|--------------|
| 1. Suite passes offline with no key | `python -m pytest -q -o addopts="" -rs` | 846 passed, 1 skipped (the `anthropic`-only test) in 107 s; with the package installed the skipped test passes too (see below). This includes `tests/test_release_docs.py`, which keeps this scorecard, the handoff and the QA report true to the repository |
| 2. The app starts without errors | `streamlit run app.py` on a scratch database, offline mode; the server under a network guard; browser smoke test | Health `ok`; no outside call from the server process; six tabs, graphs and the card render; no exceptions; no errors in the server log. The app now listens on `localhost` only |
| 3. Reset restores the identical baseline | `seed_demo.py` twice, use the database, `reset_demo.py`, compare `sqlite3 .dump` hashes | Seed, seed again, reset and a fresh seed all give `3d7c4aa4…`; a foreign file is refused with exit code 1 and left as it was |
| 4. New behaviour has tests | From Stage 13 on, the QA report records each review finding with the test that covers it. For earlier stages, the record is the commit history and the tests themselves; this gate was checked at each stage's review, not again today | Met for the findings on record; Stage 15's two fixes have regression tests that fail against the old code |
| 5. Default scenario figures | Tests and the browser | +$8.40M / +$14.00M / +$18.80M revenue, +$5.58M / +$9.50M / +$12.86M margin |
| 6. No permission leak | Criterion 12 below, including a sweep for strings that exist only in hidden records | Pass, after the capture fix below |

## Findings at the release gate

| Finding | Fix | Test |
|---------|-----|------|
| A role could learn that a hidden capture exists: capturing a transcript that someone with wider access had captured for their eyes only was refused with "This transcript has already been captured." (The duplicate check searched every source, not only the role's.) | The check now looks only at copies the role can see, so a hidden copy behaves exactly like a missing one. A role that can see the earlier copy is still refused | `test_capture_agent::test_a_hidden_copy_of_the_transcript_is_treated_as_missing` (fails with the old check) |
| The server made two outside calls at start-up. Streamlit's start-up banner looks up the machine's network address (a UDP connect to `8.8.8.8`) and, in headless mode, its public address (a request to `checkip.amazonaws.com`). The old server test ran outside the network guard, so it never saw this | `.streamlit/config.toml` sets `server.address = "localhost"`. The banner then makes no lookup, and the app no longer listens on the local network, where anyone could have opened it and picked any role | `test_e2e::test_the_streamlit_server_starts_offline_with_no_key` now loads a network guard into the server process (fails without the setting); `test_the_server_guard_refuses_and_records_an_outside_call` proves the guard works |

## Acceptance criteria

| # | Criterion (PRODUCT_SPEC §6, verbatim) | Evidence (tests re-run today) | Result | Notes |
|---|---|---|---|---|
| | **Baseline (offline, no API key, single command)** | | | |
| 1 | `streamlit run app.py` starts with no network and no `ANTHROPIC_API_KEY`. | `test_e2e::test_the_server_guard_refuses_and_records_an_outside_call`; `test_e2e::test_the_streamlit_server_starts_offline_with_no_key`; `test_smoke::test_app_renders_without_exception` | Pass | The server test now runs the real server with a network guard loaded into its process, and asserts it tried no outside call. This found and fixed two start-up calls (see Findings). Not run with Wi-Fi off. |
| 2 | Seed loads ≥ 12 fictional historical freight pricing decisions (mostly European, 1–2 non-European), ≥ 3 source types, a mix of successful, partial and failed outcomes, and at least one contradictory or poor precedent. | `test_seed::test_at_least_twelve_decisions_mostly_european`; `test_seed::test_outcomes_mix_success_partial_and_failure`; `test_seed::test_poor_and_contradictory_precedents_exist`; `test_seed::test_three_evidence_types_are_seeded` | Pass |  |
| 3 | `seed_demo.py` is idempotent; `reset_demo.py` restores byte-identical baseline query results. | `test_e2e::test_the_five_minute_demo_end_to_end_then_reset`; `test_governance_agent::test_reset_removes_governance_records`; `test_seed::test_reset_removes_user_rows_and_restores_the_baseline`; `test_seed::test_seed_twice_changes_nothing` | Pass | Also run today with the real scripts: seeding twice gives the same `.dump` hash; a database used for a capture and two pilots, once reset, has the same hash as a fresh seed. |
| 4 | Every page with numbers or advice shows the synthetic-data banner ("SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION": at the top of the page and of every stage tab, Stage 11). | all of `test_smoke`; `test_ui_guide::test_every_tab_starts_with_the_synthetic_data_banner` | Pass | Also seen today in the browser: the label at the top of the page and of all six tabs (7 times). |
| | **Capture (Agent 1)** | | | |
| 5 | The demo transcript yields one decision with owner, date, rationale, risk and exact evidence offsets; distractor discussion produces no decision. | `test_capture_agent::test_demo_transcript_yields_one_complete_decision`; `test_capture_agent::test_distractor_discussion_is_not_captured_as_a_decision`; `test_capture_agent::test_every_extracted_field_points_at_its_exact_snippet` | Pass |  |
| 6 | Missing or uncertain fields are flagged, not guessed. | `test_capture_agent::test_conflicting_owners_are_not_resolved_by_the_agent`; `test_capture_agent::test_missing_owner_is_flagged_not_guessed`; `test_capture_agent::test_unparseable_date_is_left_empty` | Pass |  |
| 7 | Only confirmed drafts are stored; confirmation writes an `AuditEvent`. | `test_capture_agent::test_capture_and_confirmation_are_audited_with_the_confirmer`; `test_capture_agent::test_extraction_alone_writes_nothing`; `test_capture_agent::test_only_a_confirmed_decision_enters_memory` | Pass |  |
| | **Memory and retrieval (Agent 2 + search)** | | | |
| 8 | Graph has typed edges and no dangling references; it is rebuilt from SQLite. | `test_memory_agent::test_edges_are_typed_and_the_main_relation_types_are_present`; `test_memory_agent::test_new_outcome_changes_the_projection`; `test_memory_agent::test_no_dangling_edges_for_any_role` | Pass |  |
| 9 | The Europe surcharge question returns the intended top precedents with score components and `[SRC-…]` citations. | `test_precedent_search::test_demo_question_returns_the_intended_precedents`; `test_precedent_search::test_every_result_shows_four_components_that_add_up`; `test_precedent_search::test_results_carry_verifiable_citations` | Pass |  |
| 10 | Unrelated regions rank lower; an unknown topic returns "insufficient verifiable evidence". | `test_precedent_search::test_unknown_topics_abstain`; `test_precedent_search::test_unrelated_regions_rank_lower` | Pass |  |
| | **Permissions** | | | |
| 11 | Filtering happens before retrieval and recommendation assembly. | `test_authz::test_sql_filter_matches_the_rule_written_in_plain_python`; `test_authz::test_view_exposes_no_write_or_raw_access`; `test_provenance::test_restricted_evidence_cannot_enter_a_recommendation` | Pass |  |
| 12 | For an unauthorised role, a `restricted_exec` record never appears in search results, graph nodes/labels/edges, citations, impact items or recommendation reasons. | `test_authz::test_nothing_retrievable_mentions_a_hidden_record`; `test_authz::test_the_sweep_would_catch_a_leak`; `test_capture_agent::test_a_hidden_copy_of_the_transcript_is_treated_as_missing`; `test_e2e::test_restricted_work_never_reaches_other_roles`; `test_governance_agent::test_a_restricted_pilot_is_invisible_to_other_roles`; `test_memory_agent::test_no_hidden_ids_labels_or_notes_in_the_graph`; `test_precedent_search::test_no_hidden_content_in_any_search_output` | Pass | The release review found one more place where a hidden record showed through, the capture duplicate check, and fixed it (see Findings). Role switching is a demo control, not security. Record IDs are sequential, so a gap can show that a hidden record exists (the seeded `DEC-015`). |
| | **Impact (Agent 3)** | | | |
| 13 | Lists affected teams, systems, customer groups, risks, cost range and effort, each marked `source_supported` or `assumed_for_demo` with confidence and rationale. | `test_impact_agent::test_cost_and_effort_are_assumed_ranges_summed_over_affected_systems`; `test_impact_agent::test_every_item_is_labelled_and_cited_or_assumed` | Pass |  |
| 14 | Flags high-volume contract customers and competitive response in Germany from retrieved precedents. | `test_impact_agent::test_flags_contract_customers_and_german_competition_from_precedents` | Pass |  |
| | **Scenarios (Agent 4)** | | | |
| 15 | Defaults compute +$8.4M / +$14.0M / +$18.8M revenue and +$5.58M / +$9.50M / +$12.86M margin. | all of `test_e2e`; `test_e2e::test_the_five_minute_demo_end_to_end_then_reset`; `test_scenario_agent::test_defaults_compute_the_documented_figures`; `test_ui_scenarios::test_default_figures_formulas_and_labels` | Pass | Also seen today in the browser: +$8.40M / +$14.00M / +$18.80M and +$5.58M / +$9.50M / +$12.86M. |
| 16 | Editing any assumption recomputes instantly; formulas and inputs are shown; no headline number is hard-coded. | `test_demo_guide::test_the_revenue_talk_track_uses_the_figures_on_screen`; `test_scenario_agent::test_editing_any_input_recomputes`; `test_scenario_agent::test_figures_follow_the_formulas_for_any_inputs` | Pass |  |
| 17 | Retention is shown as a labelled proxy; SLA shows "unchanged / not modeled". | `test_ui_scenarios::test_default_figures_formulas_and_labels` | Pass |  |
| | **Recommendation (Agent 5)** | | | |
| 18 | Default output is a phased +5% pilot; +7% is excluded with the guardrail reason; +3% is noted as lower uplift. | all of `test_e2e`; `test_e2e::test_the_five_minute_demo_end_to_end_then_reset`; `test_recommendation_agent::test_demo_question_recommends_a_phased_five_percent_pilot`; `test_recommendation_agent::test_seven_percent_is_excluded_by_the_guardrail_and_three_is_lower_uplift` | Pass | Also seen today in the browser: "Advisory recommendation: Phased pilot at +5%". |
| 19 | Every reason cites a visible source; missing evidence produces "insufficient evidence to recommend". | `test_recommendation_agent::test_every_reason_cites_exact_excerpts_the_role_can_see`; `test_recommendation_agent::test_no_precedents_means_insufficient_evidence_to_recommend`; `test_recommendation_agent::test_precedents_that_back_no_option_mean_insufficient_evidence` | Pass |  |
| 20 | Output is advisory and names the human approval requirement. | `test_recommendation_agent::test_action_plan_is_for_the_approver_and_executes_nothing`; `test_ui_recommendation::test_demo_card_is_a_phased_pilot_with_cited_reasons` | Pass |  |
| | **Governance (Agent 6)** | | | |
| 21 | `analyst` cannot approve; `pricing_director` can (simulated, labelled as such). | `test_e2e::test_the_analyst_can_track_but_not_decide`; `test_governance_agent::test_analyst_cannot_approve_or_reject_and_nothing_is_stored`; `test_ui_governance::test_the_analyst_sees_why_there_is_no_approval_form` | Pass |  |
| 22 | Approval creates a 60-day tracker with milestones and 8 weekly KPI slots. | `test_governance_agent::test_approval_opens_a_60_day_tracker_with_8_weekly_slots` | Pass |  |
| 23 | Entering a KPI update and closing the pilot attaches an `Outcome` to the decision, visible in graph and precedent search after refresh. | all of `test_e2e`; `test_e2e::test_the_five_minute_demo_end_to_end_then_reset`; `test_governance_agent::test_the_outcome_shows_in_search_and_graph_after_refresh`; `test_ui_governance::test_kpi_then_close_writes_the_outcome_into_memory` | Pass |  |
| 24 | Invalid state transitions are refused; every change is audited. | `test_governance_agent::test_every_record_the_flow_writes_has_an_audit_event`; `test_governance_agent::test_only_listed_transitions_are_allowed` | Pass | Every governance change is audited. The demo reset is not: the rebuilt database must be the baseline byte for byte, so the reset is logged instead. |
| | **Safety** | | | |
| 25 | No outbound calls, emails, tickets, price changes or payments anywhere in the code. | `test_safety::test_no_code_imports_a_way_out`; `test_safety::test_no_code_runs_programs_or_imports_by_name` | Pass |  |
| 26 | Instructions embedded in source documents are treated as text and have no effect. | `test_e2e::test_a_hostile_document_is_text_everywhere_and_changes_nothing`; `test_impact_agent::test_instructions_in_a_source_are_quoted_not_followed`; `test_provenance::test_instructions_inside_a_document_change_nothing`; `test_ui_capture::test_pasted_markup_and_instructions_are_displayed_as_plain_text` | Pass |  |
| 27 | No secrets in the repo; `.env`, `.venv`, `data/*.db` are git-ignored. | `test_safety::test_env_example_holds_placeholders_only`; `test_safety::test_env_files_databases_and_venv_are_not_tracked`; `test_safety::test_no_secrets_in_tracked_or_new_files`; `test_safety::test_the_secret_scan_flags_keys_and_allows_fake_env_lines_in_tests` | Pass | The scan covers tracked files and new files git would pick up. |
| 28 | Optional Anthropic mode (if built) falls back to offline on missing key, timeout or validation failure; tests use mocks only. (Stage 12: `llm_gateway.py` + `brief.py`; the model only rewords tab 5's cited reasons into a brief, on request, and the brief is checked against the card's sources and numbers.) | all of `test_brief`; all of `test_llm_gateway`; all of `test_ui_brief` | Pass | Fake clients only; 1 test (SDK error classes) is skipped without the optional package and was not run today. The live API has never been called. The checks catch invented or borrowed numbers and unknown sources, not a misleading paraphrase. |

## Coding standards (`CLAUDE.md`)

| Rule | Check today | Result |
|------|-------------|--------|
| Dependencies limited to the stated stack | `requirements.txt`: streamlit, pydantic, networkx, plotly, pytest. `requirements-optional.txt`: anthropic | Met |
| Offline by default; no invented model ID | `LLM_MODE=offline` in `.env.example`; the only model ID in code is the configured default `claude-opus-5-5`, overridable with `DM_LLM_MODEL` | Met |
| Secrets from the environment only | `.env.example` holds placeholders; secret scan over tracked and new files; `.env`, `.venv/`, `data/*.db` git-ignored | Met |
| Parameterised SQL only | Every value is a `?` or `:name` parameter. Five statements put a table or column name into the SQL text; each name comes from a module constant, and filter columns are checked against an allow-list. The two `PRAGMA` lines use fixed integers | Met |
| No `print` in library code; logging only | No `print(` in `src/` or `app.py` | Met |
| Source text never rendered as HTML | No `unsafe_allow_html`, `st.html` or HTML components (safety test over every code file) | Met |
| No randomness or wall clock in agents | No `random` and no `datetime.now` / `date.today` outside `ui/` | Met |
| `ruff check .` | Run today | All checks passed |
| `ruff format --check .` | Run today | 22 files from earlier stages would be reformatted; files changed since Stage 13 are formatted, except where a fix kept the surrounding code as it was. Formatting is optional tooling here |

## Release-gate verification of the open items

After the gate, the items left open were worked through on 2026-11-17.

| Item | What was done | Result |
|------|---------------|--------|
| The suite with the optional `anthropic` package | Installed `requirements-optional.txt` (anthropic 2.9.0) and ran the full suite | 845 passed, 0 skipped (before the two backup tests were added): the SDK error-class test that is skipped without the package passes. The package was then uninstalled again, so the default setup stays without it |
| A browser session on a guarded server | Started the app with the network guard loaded into the server process, on fresh baseline data, and ran the whole demo in the browser: capture, ask, exclusion, scenarios, recommendation, approval, week 1, close, outcome in tab 2 | The guard log holds only "guard loaded": the server tried no outside call during the session. Every page request went to `localhost`; none used an outside address. No errors in the server log |
| Timing | Each click in that run was timed in the page, and speaking time was estimated from the script's words | The app answers each click in about 0.2–0.4 s, about 3 s across the 12 clicks (measured in the page from each click until Streamlit's running indicator cleared, polled every 50 ms, so the figures are approximate). About 340 words of speech: roughly 2 to 2.5 minutes at 130–160 words a minute. Step 6 is the tight one, so the checklist now fills in its name and markets before the demo; a second run confirmed both fields keep their values through steps 1–5 and approval then works |
| Backup | Ten frames of that run saved in [demo_backup/](demo_backup/README.md), shown through an offline slideshow ([slides.html](demo_backup/slides.html)) that keeps the synthetic-data label above every frame | A still-frame fallback for a failed laptop or projector. Checked in a browser: all ten frames load, the label stays on each, and the page requests nothing outside its own folder |

## Not verified

| Item | Why | What to do |
|------|-----|------------|
| A person timing the five-minute demo aloud | It needs a presenter. The timing above is measured for the app and estimated for speech | Rehearse aloud with a stopwatch, twice ([DEMO_CHECKLIST.md](DEMO_CHECKLIST.md)) |
| A narrated backup recording | It needs the presenter's voice and screen. The still frames are a fallback, not a recording | Record one clean run before presenting |
| The demo with Wi-Fi actually off | Turning Wi-Fi off is a change to the laptop's settings, which was not made. The guarded run above shows the app needs no network: the server made no outside call and the page loaded only from `localhost` | Confirm it once in your own rehearsal |
| The live Anthropic API | It needs an API key, and none is set on this machine (no `.env`, nothing in the environment). The call costs money and must use your key, which you set, not one typed into chat | Add your key to `.env` (`ANTHROPIC_API_KEY=…`, `LLM_MODE=anthropic`), install `requirements-optional.txt`, run the app and click **Reword with Claude** in tab 5 once |
| Python versions other than 3.15.2; other browsers, Windows, larger data | Out of scope for the MVP | See [HANDOFF.md](HANDOFF.md) |
