"""Agent 5: option rules, cited reasons, abstention and role filtering."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from decision_memory import DEMO_QUESTION
from decision_memory.agents.impact_agent import HVC_EXCLUSION, assess_impact, draft_proposal
from decision_memory.agents.recommendation_agent import (
    DEFAULT_RULES,
    INSUFFICIENT_TO_RECOMMEND,
    PolicyCite,
    PrecedentFact,
    RecommendationRules,
    judge_options,
    key_risks,
    precedent_facts,
    recommend,
    rules_summary,
)
from decision_memory.agents.scenario_agent import default_inputs, scenario_set, simulate_scenarios
from decision_memory.authz import MemoryView
from decision_memory.models import (
    EvidenceBasis,
    ImpactAssessment,
    OptionCheck,
    OutcomeVerdict,
    RecommendationResult,
    RecommendationType,
    RiskLevel,
    Role,
)
from decision_memory.precedent_search import infer_query, search_precedents
from decision_memory.provenance import resolve_all, verify_recommendation
from decision_memory.repository import Repository

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR

AS_OF = date(2026, 11, 14)
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
DEMO_TOP = ["DEC-009", "DEC-007", "DEC-004", "DEC-011", "DEC-001"]


def run(
    repo: Repository,
    role: Role = Role.PRICING_DIRECTOR,
    ids: list[str] | tuple[str, ...] = tuple(DEMO_TOP),
    *,
    region: str | None = "Europe",
    exclusions: tuple[str, ...] = (),
    impact: ImpactAssessment | None | str = "assess",
    rules: RecommendationRules = DEFAULT_RULES,
    **input_changes,
) -> RecommendationResult:
    """The whole chain for one role: proposal, impact, scenarios, recommendation."""
    view = MemoryView(repo, role)
    proposal = draft_proposal(
        DEMO_QUESTION, region, AS_OF, NOW, surcharge_rate=0.05, exclusions=exclusions
    )
    if impact == "assess":
        impact = assess_impact(view, proposal, ids)
    inputs = default_inputs(proposal.surcharge_options).model_copy(update=input_changes)
    scenarios = scenario_set(view, inputs, proposal.id, NOW)
    return recommend(view, proposal, ids, impact, scenarios, NOW, rules)


def by_rate(result: RecommendationResult) -> dict[float, OptionCheck]:
    return {o.surcharge_rate: o for o in result.options}


def reason_texts(result: RecommendationResult, prefix: str = "") -> list[str]:
    return [r.text for r in result.recommendation.reasons if r.text.startswith(prefix)]


# --- the gate -------------------------------------------------------------------


def test_demo_question_recommends_a_phased_five_percent_pilot(seeded: Repository) -> None:
    view = MemoryView(seeded, Role.PRICING_DIRECTOR)
    assert search_precedents(view, infer_query(DEMO_QUESTION, AS_OF)).precedent_ids == DEMO_TOP
    result = run(seeded)
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.PHASED_PILOT
    assert rec.recommended_rate == 0.05
    assert rec.approval_required_by is Role.PRICING_DIRECTOR
    assert rec.risk_level is RiskLevel.MEDIUM
    assert result.precedent_ids == DEMO_TOP
    assert result.message == (
        "Phased pilot at +5%. Advisory only: approval by a named Pricing Director is required."
    )
    options = by_rate(result)
    assert [rate for rate, o in options.items() if o.chosen] == [0.05]
    assert options[0.05].supporting_decision_ids == ["DEC-004"]  # France, the one success
    assert options[0.05].caution_decision_ids == ["DEC-009", "DEC-007"]  # partial outcomes
    assert options[0.03].supporting_decision_ids == ["DEC-011"]


def test_seven_percent_is_excluded_by_the_guardrail_and_three_is_lower_uplift(
    seeded: Repository,
) -> None:
    excluded = {o.surcharge_rate: o.reason for o in run(seeded).recommendation.excluded_options}
    assert set(excluded) == {0.03, 0.07}
    assert excluded[0.07] == (
        "Excluded: churn loss 1.6% exceeds the 1.2% guardrail; no successful precedent within "
        "±0.5 points of this rate among those found for this question."
    )
    assert excluded[0.03] == "Not chosen: lower uplift (+$8.40M against +$14.00M at +5%)."


@pytest.mark.parametrize("role", list(Role))
def test_every_reason_cites_exact_excerpts_the_role_can_see(seeded: Repository, role) -> None:
    view = MemoryView(seeded, role)
    rec = run(seeded, role).recommendation
    assert len(rec.reasons) == 8
    for reason in rec.reasons:
        assert reason.basis is EvidenceBasis.SOURCE_SUPPORTED
        cited = resolve_all(view, reason.source_refs)
        assert cited
        for ref in reason.source_refs:
            body = view.source(ref.source_id).body
            assert body[ref.start_offset : ref.end_offset] == ref.excerpt
    verify_recommendation(view, rec)


def test_reasons_are_precedent_cautions_guardrail_policies_and_key_risks(
    seeded: Repository,
) -> None:
    result = run(seeded)
    texts = reason_texts(result)
    assert texts[0] == (
        "Successful precedent at +5%: DEC-004, France. Close to plan. Contract exemption, "
        "six weeks' notice and weekly KPI checks were credited."
    )
    assert [t.split(".")[0] for t in texts[1:3]] == [
        "Caution at +5%: DEC-009, UK and Ireland (partial)",
        "Caution at +5%: DEC-007, Italy (partial)",
    ]
    assert all("It does not back this option." in t for t in texts[1:3])
    assert texts[3] == (
        "Guardrail: churn loss must stay within 1.2% of baseline revenue. Exceeded by +7% (1.6%)."
    )
    assert texts[4].startswith("Policy: every surcharge above 3% starts as a phased pilot")
    assert texts[5].startswith("Policy: high-volume contract customers are excluded")
    assert texts[6:] == [
        (
            "Key risk: Churn among high-volume contract customers (confidence high; quoted from "
            "DEC-001, which failed)."
        ),
        (
            "Key risk: Competitive response in Germany (confidence medium; quoted from DEC-001, "
            "which failed)."
        ),
    ]
    (guardrail,) = [r for r in result.recommendation.reasons if r.text.startswith("Guardrail")]
    assert [ref.source_id for ref in guardrail.source_refs] == ["SRC-014"]
    policies = [r for r in result.recommendation.reasons if r.text.startswith("Policy")]
    assert [[ref.source_id for ref in r.source_refs] for r in policies] == [
        ["SRC-014"],
        ["SRC-022"],
    ]


def test_action_plan_is_for_the_approver_and_executes_nothing(seeded: Repository) -> None:
    result = run(seeded)
    plan = result.recommendation.action_plan
    assert plan[0] == (
        "Exclude high-volume contract customers; apply any change to them only at contract "
        "renewal (the draft proposal does not yet: add the exclusion in tab 3)."
    )
    assert plan[1] == (
        "Run +5% as a phased pilot in 2 markets, chosen by the approver, before any wider rollout."
    )
    assert "Pause and review if churn loss exceeds 1.2% of baseline revenue." in plan
    assert "Hold an executive review after 60 days." in plan
    assert (
        "Manage the key risks: Churn among high-volume contract customers, Competitive "
        "response in Germany."
    ) in plan
    assert plan[-1] == (
        "Human approval required: a named Pricing Director approves or rejects. This app "
        "executes nothing."
    )
    assert result.required_exclusion == HVC_EXCLUSION and not result.exclusion_in_proposal
    met = run(seeded, exclusions=(HVC_EXCLUSION,))
    assert met.exclusion_in_proposal
    assert "the draft proposal already does" in met.recommendation.action_plan[0]


def test_figures_and_demo_settings_are_assumptions_never_reasons(seeded: Repository) -> None:
    rec = run(seeded).recommendation
    assert not any("$" in r.text for r in rec.reasons)
    assert rec.assumptions[1] == (
        "+5%: revenue +$14.00M, margin +$9.50M, churn loss 1% of baseline (computed in tab 4 "
        "from editable synthetic inputs)."
    )
    assert "Baseline revenue (USD M per quarter): $400.00M (assumed for demo)." in rec.assumptions
    assert "Pilot scope of 2 markets is a demo setting, not taken from any source." in (
        rec.assumptions
    )
    assert any(a.startswith("Evidence rule:") and "±0.5 points" in a for a in rec.assumptions)
    assert "not a forecast or an authorised decision" in rec.uncertainty
    assert "4 of 8 assumed for demo" in rec.uncertainty


def test_recommendation_is_deterministic(seeded: Repository) -> None:
    assert run(seeded).model_dump() == run(seeded).model_dump()


# --- the rules respond to their inputs --------------------------------------------


def test_a_tighter_guardrail_recommends_three_percent_without_a_pilot(seeded: Repository) -> None:
    result = run(seeded, churn_guardrail=0.009)
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.PROCEED and rec.recommended_rate == 0.03
    assert reason_texts(result, "Successful precedent") == [
        (
            "Successful precedent at +3%: DEC-011, Germany. Slightly ahead of plan with no "
            "competitive response at the 3% level."
        )
    ]
    assert not reason_texts(result, "Policy: every surcharge above")
    assert not any("Pilot scope" in a for a in rec.assumptions)
    assert not any("executive review" in step for step in rec.action_plan)
    assert rec.action_plan[1] == "Apply +3% from 2027-Q1 (proposal scope: Europe)."


def test_an_edited_guardrail_is_an_assumption_not_a_cited_reason(seeded: Repository) -> None:
    result = run(seeded, churn_guardrail=0.02)
    rec = result.recommendation
    assert not reason_texts(result, "Guardrail")
    assert "Churn guardrail (share of baseline): 2% (assumed for demo, edited)." in rec.assumptions
    # +7% is now within the guardrail, but nothing successful backs it: +5% still wins.
    assert rec.recommended_rate == 0.05
    assert by_rate(result)[0.07].verdict == (
        "Excluded: no successful precedent within ±0.5 points of this rate among those found for this question."
    )


def test_backed_options_that_all_break_a_rule_mean_do_not_proceed(seeded: Repository) -> None:
    churn = {0.03: 0.013, 0.05: 0.013, 0.07: 0.016}
    result = run(seeded, churn_loss_by_rate=churn)
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.DO_NOT_PROCEED
    assert rec.recommended_rate is None and not rec.action_plan and rec.risk_level is None
    assert result.message.startswith("Do not proceed")
    assert reason_texts(result) == [
        (
            "Guardrail: churn loss must stay within 1.2% of baseline revenue. Exceeded by "
            "+3% (1.3%), +5% (1.3%), +7% (1.6%)."
        )
    ]
    assert not any(o.chosen for o in result.options)


def test_a_failed_precedent_near_the_chosen_rate_makes_the_risk_high(seeded: Repository) -> None:
    wide = DEFAULT_RULES.model_copy(update={"rate_tolerance": 0.01})  # 6% now counts as near 5%
    result = run(seeded, rules=wide)
    assert result.recommendation.risk_level is RiskLevel.HIGH
    assert by_rate(result)[0.05].caution_decision_ids == ["DEC-009", "DEC-007", "DEC-001"]
    assert reason_texts(result, "Caution at +6%: DEC-001, Germany (failed).")


def test_a_clean_precedent_and_no_key_risk_is_low_risk(seeded: Repository) -> None:
    result = run(seeded, ids=["DEC-003"])  # Benelux 3% pilot, a success
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.PROCEED and rec.recommended_rate == 0.03
    assert rec.risk_level is RiskLevel.LOW
    assert not reason_texts(result, "Key risk") and not reason_texts(result, "Caution")


def test_partial_precedents_back_an_option_only_if_the_rules_say_so(seeded: Repository) -> None:
    lenient = DEFAULT_RULES.model_copy(
        update={"supporting_verdicts": (OutcomeVerdict.SUCCESS, OutcomeVerdict.PARTIAL)}
    )
    result = run(seeded, rules=lenient)
    assert by_rate(result)[0.05].supporting_decision_ids == ["DEC-009", "DEC-007", "DEC-004"]
    assert not by_rate(result)[0.05].caution_decision_ids
    assert reason_texts(result, "Backing precedent (partial) at +5%: DEC-009")


@pytest.mark.parametrize("missing", ["none", "abstained"])
def test_without_an_impact_analysis_key_risks_are_reported_as_not_assessed(
    seeded: Repository, missing: str
) -> None:
    impact = None
    if missing == "abstained":
        view = MemoryView(seeded, Role.PRICING_DIRECTOR)
        impact = assess_impact(view, draft_proposal(DEMO_QUESTION, "Europe", AS_OF, NOW), [])
        assert impact.abstained
    result = run(seeded, impact=impact)
    rec = result.recommendation
    assert rec.recommended_rate == 0.05 and rec.risk_level is RiskLevel.MEDIUM
    assert not reason_texts(result, "Key risk")
    assert "No impact analysis is available, so key risks were not assessed." in rec.assumptions
    assert not any(step.startswith("Manage the key risks") for step in rec.action_plan)


def test_policies_written_for_europe_are_flagged_elsewhere(seeded: Repository) -> None:
    line = (
        "The cited policies were written for Europe; applying them to Asia-Pacific is a demo "
        "assumption."
    )
    assert line in run(seeded, region="Asia-Pacific").recommendation.assumptions
    assert line not in run(seeded).recommendation.assumptions
    assert not any("written for Europe" in a for a in run(seeded).recommendation.assumptions)


def test_scenarios_for_another_proposal_or_other_options_are_refused(seeded: Repository) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    proposal = draft_proposal(DEMO_QUESTION, "Europe", AS_OF, NOW, surcharge_rate=0.05)
    other_id = scenario_set(view, default_inputs(), "PRO-OTHER", NOW)
    other_options = scenario_set(view, default_inputs([0.03, 0.04, 0.05]), proposal.id, NOW)
    for scenarios in (other_id, other_options):
        with pytest.raises(ValueError, match="not computed for this proposal"):
            recommend(view, proposal, DEMO_TOP, None, scenarios, NOW)


# --- judging options (pure) ---------------------------------------------------------


def fact(decision_id: str, rate: float | None, verdict: OutcomeVerdict | None) -> PrecedentFact:
    return PrecedentFact(
        decision_id=decision_id, market="Testland", surcharge_rate=rate, verdict=verdict
    )


def results_for(churn: dict[float, float] | None = None):
    """Default scenario results, with the churn loss of some options replaced."""
    by_rate = {0.03: 0.006, 0.05: 0.010, 0.07: 0.016} | (churn or {})
    inputs = default_inputs().model_copy(update={"churn_loss_by_rate": by_rate})
    return simulate_scenarios(inputs, "PRO-DRAFT", NOW)


def test_judge_options_breaks_ties_on_the_lower_rate() -> None:
    facts = [
        fact("DEC-A", 0.03, OutcomeVerdict.SUCCESS),
        fact("DEC-B", 0.05, OutcomeVerdict.SUCCESS),
    ]
    # +3% with no churn and +5% with 1.8% churn both come to +$10.80M.
    results = results_for({0.03: 0.0, 0.05: 0.018})
    assert results[0].revenue_change_musd == results[1].revenue_change_musd == pytest.approx(10.8)
    checks = judge_options(results, 0.02, facts)
    assert [c.surcharge_rate for c in checks if c.chosen] == [0.03]


def test_judge_options_only_counts_precedents_with_a_rate_and_an_outcome() -> None:
    facts = [
        fact("DEC-NO-OUTCOME", 0.05, None),
        fact("DEC-NO-RATE", None, OutcomeVerdict.SUCCESS),
        fact("DEC-FAR", 0.056, OutcomeVerdict.SUCCESS),  # 0.6 points away
        fact("DEC-EDGE", 0.055, OutcomeVerdict.SUCCESS),  # exactly 0.5 points away
    ]
    five = judge_options(results_for(), 0.012, facts)[1]
    assert five.supporting_decision_ids == ["DEC-EDGE"] and not five.caution_decision_ids


def test_judge_options_excludes_an_option_with_no_upside() -> None:
    facts = [fact("DEC-A", 0.03, OutcomeVerdict.SUCCESS)]
    three = judge_options(results_for({0.03: 0.03}), 0.05, facts)[0]  # 10.8 − 12 = −1.2
    assert not three.eligible and not three.has_upside
    assert three.verdict == "Excluded: no revenue upside (−$1.20M)."


# --- abstention and permissions ---------------------------------------------------


@pytest.mark.parametrize("ids", [[], ["DEC-999"]])
def test_no_precedents_means_insufficient_evidence_to_recommend(
    seeded: Repository, ids: list[str]
) -> None:
    result = run(seeded, ids=ids)
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.INSUFFICIENT_EVIDENCE
    assert rec.recommended_rate is None and not rec.reasons and not rec.action_plan
    assert result.message.startswith(INSUFFICIENT_TO_RECOMMEND)
    assert not result.options and not result.precedent_ids


def test_precedents_that_back_no_option_mean_insufficient_evidence(seeded: Repository) -> None:
    # DEC-001 (6%, failed) and DEC-008 (0%, partial) back none of +3 / +5 / +7 %.
    result = run(seeded, ids=["DEC-001", "DEC-008"])
    assert result.recommendation.decision_type is RecommendationType.INSUFFICIENT_EVIDENCE
    assert result.message.startswith(INSUFFICIENT_TO_RECOMMEND)
    assert result.options and all(not o.supporting_decision_ids for o in result.options)
    assert not result.recommendation.reasons


def test_backing_precedents_that_cannot_be_cited_mean_no_advice(
    seeded: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "decision_memory.agents.recommendation_agent.resolve_all", lambda view, refs: None
    )
    result = run(seeded)
    rec = result.recommendation
    assert rec.decision_type is RecommendationType.INSUFFICIENT_EVIDENCE
    assert rec.recommended_rate is None and not rec.reasons and not rec.action_plan
    assert "could not be cited" in result.message
    assert not any(o.chosen for o in result.options)
    assert by_rate(result)[0.05].verdict.startswith("Not recommended")


@pytest.mark.parametrize(
    ("role", "hidden_ids", "secrets"),
    [
        (Role.PRICING_DIRECTOR, ["DEC-015"], HIDDEN_FROM_DIRECTOR),
        (Role.ANALYST, ["DEC-015", "DEC-016"], HIDDEN_FROM_ANALYST),
    ],
)
def test_hidden_precedents_are_ignored_like_unknown_ones(
    seeded: Repository, role: Role, hidden_ids: list[str], secrets: tuple[str, ...]
) -> None:
    plain = run(seeded, role).model_dump()
    with_hidden = run(seeded, role, [*DEMO_TOP, *hidden_ids])
    assert with_hidden.model_dump() == plain
    assert run(seeded, role, [*DEMO_TOP, "DEC-998"]).model_dump() == plain
    assert run(seeded, role, hidden_ids).model_dump() == run(seeded, role, ["DEC-999"]).model_dump()
    dumped = with_hidden.model_dump_json()
    for secret in secrets:
        assert secret not in dumped
    assert precedent_facts(MemoryView(seeded, role), hidden_ids) == []


def test_restricted_precedent_backs_no_surcharge_option(seeded: Repository) -> None:
    # DEC-015 holds prices (0%): visible to the executive, but it backs no option.
    result = run(seeded, Role.RESTRICTED_EXEC, ["DEC-015"])
    assert result.recommendation.decision_type is RecommendationType.INSUFFICIENT_EVIDENCE
    assert result.precedent_ids == ["DEC-015"]


@pytest.mark.parametrize(
    "cite",
    [
        PolicyCite(source_id="SRC-019", excerpt="CONFIDENTIAL: Board Strategy Committee only"),
        PolicyCite(source_id="SRC-014", excerpt="This sentence is not in the source."),
    ],
)
def test_a_policy_that_cannot_be_cited_is_an_assumption_not_a_reason(
    seeded: Repository, cite: PolicyCite
) -> None:
    rules = DEFAULT_RULES.model_copy(update={"exclusion_policy": cite})
    result = run(seeded, Role.ANALYST, rules=rules)
    rec = result.recommendation
    assert rec.recommended_rate == 0.05  # the rule still applies
    assert not reason_texts(result, "Policy: high-volume contract customers")
    assert (
        "Excluding high-volume contract customers is applied as a demo rule: no source for it "
        "is available here."
    ) in rec.assumptions
    assert "SRC-019" not in rec.model_dump_json() and "Board" not in rec.model_dump_json()


def test_key_risks_need_a_failed_precedent_and_enough_confidence(seeded: Repository) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    impact = assess_impact(view, draft_proposal(DEMO_QUESTION, "Europe", AS_OF, NOW), DEMO_TOP)
    assert [i.key for i in key_risks(impact)] == ["risk.hvc_churn", "risk.competition_de"]
    low_bar = DEFAULT_RULES.model_copy(update={"key_risk_min_confidence": "low"})
    assert "risk.contract_clauses" in [i.key for i in key_risks(impact, low_bar)]
    assert "risk.billing" not in [i.key for i in key_risks(impact, low_bar)]  # partial only
    assert key_risks(None) == []


# --- rules and contracts ----------------------------------------------------------


def test_default_rules_match_the_policies_they_cite(seeded: Repository) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    for cite in (DEFAULT_RULES.pilot_policy, DEFAULT_RULES.exclusion_policy):
        source = view.source(cite.source_id)
        assert source is not None and source.body.count(cite.excerpt) == 1
    pilot = DEFAULT_RULES.pilot_policy.excerpt
    assert f"above {DEFAULT_RULES.pilot_above_rate * 100:g}%" in pilot
    assert f"{DEFAULT_RULES.review_after_days}-day" in pilot
    assert DEFAULT_RULES.required_exclusion == HVC_EXCLUSION
    assert DEFAULT_RULES.required_exclusion.lower() in DEFAULT_RULES.exclusion_policy.excerpt
    assert DEFAULT_RULES.policy_region in DEFAULT_RULES.exclusion_policy.excerpt
    assert DEFAULT_RULES.supporting_verdicts == (OutcomeVerdict.SUCCESS,)


def test_default_rules_hold_nothing_from_restricted_records() -> None:
    dumped = DEFAULT_RULES.model_dump_json()
    for secret in HIDDEN_FROM_ANALYST:
        assert secret not in dumped


def test_option_check_contract() -> None:
    base = {
        "surcharge_rate": 0.05, "revenue_change_musd": 1.0, "margin_change_musd": 0.5,
        "churn_loss_fraction": 0.01, "verdict": "v",
    }  # fmt: skip
    ok = {"within_guardrail": True, "has_upside": True, "supporting_decision_ids": ["DEC-001"]}
    assert OptionCheck(**base, **ok, eligible=True, chosen=True).chosen
    with pytest.raises(ValidationError, match="eligible must equal"):
        OptionCheck(**base, **ok, eligible=False)
    with pytest.raises(ValidationError, match="eligible must equal"):
        OptionCheck(**base, within_guardrail=True, has_upside=True, eligible=True)
    with pytest.raises(ValidationError, match="Only an eligible option"):
        OptionCheck(**base, within_guardrail=False, has_upside=True, eligible=False, chosen=True)


def test_result_contract_ties_the_card_to_the_options(seeded: Repository) -> None:
    result = run(seeded)
    rec = result.recommendation
    with pytest.raises(ValidationError, match="must give cited reasons"):
        RecommendationResult(
            recommendation=rec.model_copy(update={"reasons": []}),
            options=result.options,
            message="m",
        )
    with pytest.raises(ValidationError, match="chosen option must be the recommended rate"):
        RecommendationResult(
            recommendation=rec.model_copy(update={"recommended_rate": 0.03}),
            options=result.options,
            message="m",
        )
    with pytest.raises(ValidationError, match="Only a pilot or proceed"):
        RecommendationResult(
            recommendation=rec.model_copy(
                update={"decision_type": RecommendationType.DO_NOT_PROCEED}
            ),
            options=result.options,
            message="m",
        )


# --- review fixes -------------------------------------------------------------------


def test_key_risk_reasons_cite_only_lines_from_the_failed_precedents(seeded: Repository) -> None:
    # Regression: they also cited lines from successful precedents, such as
    # "No competitive response was observed at the 3% level."
    risks = [r for r in run(seeded).recommendation.reasons if r.text.startswith("Key risk")]
    assert len(risks) == 2
    for reason in risks:
        assert [ref.source_id for ref in reason.source_refs] == ["SRC-002"]  # DEC-001's outcome
        assert all("No competitive response" not in ref.excerpt for ref in reason.source_refs)


def test_pilot_plan_does_not_track_against_the_full_baseline_figure(seeded: Repository) -> None:
    # Regression: a two-market pilot was told to track against +$14.00M a quarter.
    rec = run(seeded).recommendation
    assert not any("$" in step for step in rec.action_plan)
    assert rec.action_plan[2].startswith(
        "Check weekly that churn loss in the pilot markets stays at the planned 1%"
    )
    assert any(
        a.startswith("The scenario figures cover the whole baseline") for a in rec.assumptions
    )
    proceed = run(seeded, churn_guardrail=0.009).recommendation  # +3%, not a pilot
    assert "(+$8.40M a quarter, churn loss 0.6% of baseline)" in proceed.action_plan[2]
    assert not any("whole baseline" in a for a in proceed.assumptions)


def test_evidence_claims_are_limited_to_the_precedents_found(seeded: Repository) -> None:
    # Regression: asked at 4%, search does not return France, and +5% was said to have
    # "no successful precedent" as if memory held none.
    view = MemoryView(seeded, Role.PRICING_DIRECTOR)
    query = infer_query(
        "Should we increase the freight surcharge by 4% for European customers?", AS_OF
    )
    found = search_precedents(view, query).precedent_ids
    assert "DEC-004" not in found and "DEC-005" in found
    proposal = draft_proposal(query.text, query.region, AS_OF, NOW, surcharge_rate=0.04)
    scenarios = scenario_set(view, default_inputs(proposal.surcharge_options), proposal.id, NOW)
    result = recommend(view, proposal, found, None, scenarios, NOW)
    assert by_rate(result)[0.05].verdict == (
        "Excluded: no successful precedent within ±0.5 points of this rate among those found "
        "for this question."
    )
    assert any("among the precedents found for this question" in a
               for a in result.recommendation.assumptions)  # fmt: skip


def test_rules_summary_follows_the_settings() -> None:
    default = rules_summary()
    assert "within ±0.5 points of its rate whose outcome was a success" in default
    assert "partial or failed outcome are cautions" in default
    assert "Only the precedents found for this question are considered." in default
    lenient = DEFAULT_RULES.model_copy(
        update={
            "supporting_verdicts": (OutcomeVerdict.SUCCESS, OutcomeVerdict.PARTIAL),
            "rate_tolerance": 0.01,
        }
    )
    assert "within ±1 points of its rate whose outcome was a success or partial" in (
        rules_summary(lenient)
    )
    assert "a failed outcome are cautions" in rules_summary(lenient)
