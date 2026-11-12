"""Permission filtering: what each demo role can retrieve, and proof that nothing leaks."""

from __future__ import annotations

from datetime import date

import pytest

from decision_memory.authz import (
    DEFAULT_ROLE,
    MemoryView,
    accessible_decisions,
    accessible_sources,
    can_approve,
    can_view_source,
    parse_role,
)
from decision_memory.models import (
    Decision,
    DecisionStatus,
    Entity,
    Outcome,
    OutcomeVerdict,
    Proposal,
    Recommendation,
    RecommendationReason,
    RecommendationType,
    Relation,
    Role,
    SourceDocument,
)
from decision_memory.repository import Repository, RepositoryError

from conftest import (
    HIDDEN_FROM_ANALYST,
    HIDDEN_FROM_DIRECTOR,
    STAMPS,
    make_decision,
    make_source,
)

ALL_DECISIONS = {f"DEC-{n:03d}" for n in range(1, 17)}
HIDDEN = {
    Role.RESTRICTED_EXEC: (),
    Role.PRICING_DIRECTOR: HIDDEN_FROM_DIRECTOR,
    Role.ANALYST: HIDDEN_FROM_ANALYST,
}


def everything_visible(view: MemoryView) -> str:
    """All content a role can retrieve through the view, as one searchable string."""
    records = [
        *view.sources(),
        *view.decisions(),
        *view.outcomes(),
        *view.entities(),
        *view.relations(),
        *view.recommendations(),
    ]
    return "\n".join(record.model_dump_json() for record in records)


def store_recommendation_citing(repo: Repository, source_id: str, excerpt: str) -> Recommendation:
    repo.add(
        Proposal(
            id="PRO-900",
            question="Should we apply a 5% surcharge?",
            region="Europe",
            effective_quarter="2027-Q1",
            surcharge_options=[0.05],
            requested_by_role=Role.RESTRICTED_EXEC,
            **STAMPS,
        )
    )
    ref = repo.get(SourceDocument, source_id).excerpt_ref(excerpt)
    return repo.add(
        Recommendation(
            id="REC-900",
            proposal_id="PRO-900",
            decision_type=RecommendationType.PHASED_PILOT,
            recommended_rate=0.05,
            reasons=[RecommendationReason(text="Sensitivity analysis.", source_refs=[ref])],
            approval_required_by=Role.PRICING_DIRECTOR,
            **STAMPS,
        )
    )


# --- roles ------------------------------------------------------------------


def test_can_view_source_follows_the_role_tags() -> None:
    source = make_source(access_roles=[Role.PRICING_DIRECTOR, Role.RESTRICTED_EXEC])
    assert can_view_source(Role.PRICING_DIRECTOR, source)
    assert can_view_source(Role.RESTRICTED_EXEC, source)
    assert not can_view_source(Role.ANALYST, source)


@pytest.mark.parametrize("value", ["admin", "", None, "PRICING_DIRECTOR", 7])
def test_unknown_roles_fail_closed(seeded: Repository, value: object) -> None:
    assert parse_role(value) is None
    assert not can_view_source(value, make_source())
    assert not can_approve(value)
    with pytest.raises(ValueError):
        MemoryView(seeded, value)
    with pytest.raises(ValueError):
        accessible_decisions(seeded, value)


def test_default_role_and_approvers() -> None:
    assert DEFAULT_ROLE is Role.PRICING_DIRECTOR
    assert can_approve(Role.PRICING_DIRECTOR)
    assert can_approve(Role.RESTRICTED_EXEC)
    assert not can_approve(Role.ANALYST)


# --- retrieval --------------------------------------------------------------


def test_visible_sources_per_role(seeded: Repository) -> None:
    ids = {role: {s.id for s in accessible_sources(seeded, role)} for role in Role}
    assert len(ids[Role.RESTRICTED_EXEC]) == 22
    assert ids[Role.RESTRICTED_EXEC] - ids[Role.PRICING_DIRECTOR] == {"SRC-019"}
    assert ids[Role.RESTRICTED_EXEC] - ids[Role.ANALYST] == {"SRC-019", "SRC-021"}


def test_accessible_decisions_per_role(seeded: Repository) -> None:
    ids = {role: {d.id for d in accessible_decisions(seeded, role)} for role in Role}
    assert ids[Role.RESTRICTED_EXEC] == ALL_DECISIONS
    assert ids[Role.PRICING_DIRECTOR] == ALL_DECISIONS - {"DEC-015"}
    assert ids[Role.ANALYST] == ALL_DECISIONS - {"DEC-015", "DEC-016"}


def test_one_hidden_source_withholds_the_whole_decision(seeded: Repository) -> None:
    # DEC-016 cites SRC-020 (visible to the analyst) and SRC-021 (not).
    analyst = MemoryView(seeded, Role.ANALYST)
    assert analyst.source("SRC-020") is not None
    assert analyst.source("SRC-021") is None
    assert analyst.decision("DEC-016") is None
    assert MemoryView(seeded, Role.PRICING_DIRECTOR).decision("DEC-016") is not None


def test_a_hidden_record_looks_exactly_like_a_missing_one(seeded: Repository) -> None:
    director = MemoryView(seeded, Role.PRICING_DIRECTOR)
    assert director.source("SRC-019") is None and director.source("SRC-999") is None
    assert director.decision("DEC-015") is None and director.decision("DEC-999") is None
    assert director.decisions(id="DEC-015") == []
    assert director.decisions(market="Germany", customer_segment="High-volume contract customers") == []


def test_filters_apply_on_top_of_visibility(seeded: Repository) -> None:
    director = MemoryView(seeded, Role.PRICING_DIRECTOR)
    assert {d.id for d in director.decisions(market="Germany")} == {"DEC-001", "DEC-002", "DEC-011"}
    exec_view = MemoryView(seeded, Role.RESTRICTED_EXEC)
    assert {d.id for d in exec_view.decisions(market="Germany")} == {
        "DEC-001", "DEC-002", "DEC-011", "DEC-015",
    }  # fmt: skip
    with pytest.raises(ValueError, match="Cannot filter"):
        director.decisions(risks="x")


def test_drafts_are_not_institutional_memory(seeded: Repository) -> None:
    source = seeded.get(SourceDocument, "SRC-001")
    seeded.add(make_decision(source, "DEC-901", status=DecisionStatus.DRAFT, source_refs=[]))
    seeded.add(
        make_decision(
            source,
            "DEC-902",
            status=DecisionStatus.DRAFT,
            source_refs=[source.excerpt_ref("Then we need a review after the first full month.")],
        )
    )
    for role in Role:
        visible = {d.id for d in accessible_decisions(seeded, role)}
        assert not visible & {"DEC-901", "DEC-902"}


def test_outcomes_follow_their_decision_and_their_own_evidence(seeded: Repository) -> None:
    analyst = MemoryView(seeded, Role.ANALYST)
    director = MemoryView(seeded, Role.PRICING_DIRECTOR)
    assert len(MemoryView(seeded, Role.RESTRICTED_EXEC).outcomes()) == 14
    assert analyst.outcome("OUT-004") is not None

    # A new outcome for a visible decision, backed only by director-level evidence.
    hidden_ref = seeded.get(SourceDocument, "SRC-021").excerpt_ref(
        "the upper end breaches the 1.2% guardrail"
    )
    seeded.add(
        Outcome(
            id="OUT-900",
            decision_id="DEC-004",
            verdict=OutcomeVerdict.PARTIAL,
            observed_on=date(2025, 10, 14),
            explanation="Sensitivity review.",
            source_refs=[hidden_ref],
            **STAMPS,
        )
    )
    assert analyst.decision("DEC-004") is not None
    assert analyst.outcome("OUT-900") is None
    assert "OUT-900" not in {o.id for o in analyst.outcomes(decision_id="DEC-004")}
    assert director.outcome("OUT-900") is not None


def test_entities_reachable_only_through_hidden_decisions_are_hidden(seeded: Repository) -> None:
    only_via_dec_015 = {"ENT-CGR-DE-STRAT", "ENT-STK-BOARD"}
    exec_ids = {e.id for e in MemoryView(seeded, Role.RESTRICTED_EXEC).entities()}
    director_ids = {e.id for e in MemoryView(seeded, Role.PRICING_DIRECTOR).entities()}
    assert only_via_dec_015 <= exec_ids
    assert exec_ids - director_ids == only_via_dec_015
    assert "ENT-MKT-DE" in director_ids  # still visible through other German decisions


@pytest.mark.parametrize("role", list(Role))
def test_graph_inputs_have_no_hidden_nodes_labels_or_edges(seeded: Repository, role: Role) -> None:
    """What Stage 05 will build the graph from: nodes, their labels, and edges."""
    view = MemoryView(seeded, role)
    nodes = {
        **{d.id: d.title for d in view.decisions()},
        **{s.id: s.title for s in view.sources()},
        **{e.id: e.name for e in view.entities()},
        **{o.id: o.explanation for o in view.outcomes()},
    }
    edges = view.relations()
    assert edges
    for edge in edges:  # no edge dangles into something the role cannot see
        assert edge.from_id in nodes and edge.to_id in nodes
    labels = "\n".join([*nodes, *nodes.values(), *(edge.note for edge in edges)])
    for secret in HIDDEN[role]:
        assert secret not in labels


@pytest.mark.parametrize("role", list(Role))
def test_sql_filter_matches_the_rule_written_in_plain_python(seeded: Repository, role: Role) -> None:
    sources = {s.id for s in seeded.find(SourceDocument) if can_view_source(role, s)}
    decisions = {
        d.id
        for d in seeded.find(Decision)
        if d.status is DecisionStatus.CONFIRMED
        and d.source_refs
        and all(ref.source_id in sources for ref in d.source_refs)
    }
    outcomes = {
        o.id
        for o in seeded.find(Outcome)
        if o.decision_id in decisions and all(ref.source_id in sources for ref in o.source_refs)
    }
    relations = seeded.find(Relation)
    entities = {
        end
        for rel in relations
        if {rel.from_id, rel.to_id} & decisions
        for end in (rel.from_id, rel.to_id)
        if end.startswith("ENT-")
    }
    visible = sources | decisions | outcomes | entities
    edges = {rel.id for rel in relations if rel.from_id in visible and rel.to_id in visible}

    view = MemoryView(seeded, role)
    assert {s.id for s in view.sources()} == sources
    assert {d.id for d in view.decisions()} == decisions
    assert {o.id for o in view.outcomes()} == outcomes
    assert {e.id for e in view.entities()} == entities
    assert {r.id for r in view.relations()} == edges


# --- no leaks ---------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.PRICING_DIRECTOR, Role.ANALYST])
def test_nothing_retrievable_mentions_a_hidden_record(seeded: Repository, role: Role) -> None:
    store_recommendation_citing(
        seeded, "SRC-019", "A regional competitor in Germany is expected to cut contract rates"
    )
    visible = everything_visible(MemoryView(seeded, role))
    assert "DEC-004" in visible  # the sweep is looking at real content
    for secret in HIDDEN[role]:
        assert secret not in visible


def test_the_sweep_would_catch_a_leak(seeded: Repository) -> None:
    """The same strings ARE present for the role allowed to see them."""
    visible = everything_visible(MemoryView(seeded, Role.RESTRICTED_EXEC))
    for secret in HIDDEN_FROM_ANALYST:
        assert secret in visible


def test_stored_recommendation_with_hidden_evidence_is_withheld(seeded: Repository) -> None:
    store_recommendation_citing(
        seeded, "SRC-021", "the upper end breaches the 1.2% guardrail"
    )
    assert MemoryView(seeded, Role.PRICING_DIRECTOR).recommendation("REC-900") is not None
    assert MemoryView(seeded, Role.ANALYST).recommendation("REC-900") is None
    assert MemoryView(seeded, Role.ANALYST).recommendations() == []


def test_role_tag_changes_take_effect_immediately(seeded: Repository) -> None:
    director = MemoryView(seeded, Role.PRICING_DIRECTOR)
    analyst = MemoryView(seeded, Role.ANALYST)

    board_deck = seeded.get(SourceDocument, "SRC-019")
    seeded.save(board_deck.model_copy(update={"access_roles": list(Role)}))
    assert director.decision("DEC-015") is not None

    france_call = seeded.get(SourceDocument, "SRC-006")
    seeded.save(france_call.model_copy(update={"access_roles": [Role.RESTRICTED_EXEC]}))
    assert analyst.decision("DEC-004") is None
    assert analyst.outcome("OUT-004") is None
    assert not [r for r in analyst.relations() if "DEC-004" in (r.from_id, r.to_id)]


def test_only_record_types_with_a_rule_can_be_queried_by_role(seeded: Repository) -> None:
    with pytest.raises(RepositoryError, match="no visibility rule"):
        seeded.find_visible(Proposal, Role.ANALYST)


def test_view_exposes_no_write_or_raw_access() -> None:
    public = {name for name in dir(MemoryView) if not name.startswith("_")}
    assert public == {
        "sources", "source", "can_cite", "decisions", "decision", "outcomes", "outcome",
        "entities", "relations", "recommendations", "recommendation",
    }  # fmt: skip
    assert isinstance(Entity, type)
