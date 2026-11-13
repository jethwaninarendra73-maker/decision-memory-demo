"""Agent 2: the decision graph is a typed, role-filtered projection of SQLite."""

from __future__ import annotations

from datetime import date

import networkx as nx
import pytest
from pydantic import ValidationError

from decision_memory.agents.memory_agent import (
    GraphIntegrityError,
    build_decision_graph,
    edge_type_counts,
    filter_kinds,
    kind_counts,
    layout,
    neighbourhood,
    to_networkx,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    Decision,
    DecisionGraph,
    DecisionStatus,
    EdgeType,
    Entity,
    EntityKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Outcome,
    OutcomeVerdict,
    Relation,
    Role,
    SourceDocument,
)
from decision_memory.repository import Repository

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, STAMPS, make_decision

HIDDEN = {
    Role.RESTRICTED_EXEC: (),
    Role.PRICING_DIRECTOR: HIDDEN_FROM_DIRECTOR,
    Role.ANALYST: HIDDEN_FROM_ANALYST,
}


def graph_for(repo: Repository, role: Role) -> DecisionGraph:
    return build_decision_graph(MemoryView(repo, role))


def edge_keys(graph: DecisionGraph) -> set[tuple[str, str, EdgeType]]:
    return {(edge.from_id, edge.to_id, edge.edge_type) for edge in graph.edges}


def add_outcome(repo: Repository, outcome_id: str, decision_id: str, source_id: str, excerpt: str) -> Outcome:
    ref = repo.get(SourceDocument, source_id).excerpt_ref(excerpt)
    return repo.add(
        Outcome(
            id=outcome_id,
            decision_id=decision_id,
            verdict=OutcomeVerdict.PARTIAL,
            observed_on=date(2026, 9, 30),
            explanation="Pilot review.",
            source_refs=[ref],
            **STAMPS,
        )
    )


# --- projection of the baseline ---------------------------------------------


def test_full_graph_holds_every_record_and_every_stored_relation(seeded: Repository) -> None:
    graph = graph_for(seeded, Role.RESTRICTED_EXEC)
    counts = kind_counts(graph)
    assert counts[NodeKind.DECISION] == 16
    assert counts[NodeKind.OUTCOME] == 14
    # 47 entities are stored; ENT-SEG-SME is linked to no decision, so no role sees it.
    entity_nodes = {
        node.id for node in graph.nodes if node.kind.value in {kind.value for kind in EntityKind}
    }
    assert entity_nodes == {e.id for e in seeded.find(Entity)} - {"ENT-SEG-SME"}
    assert counts[NodeKind.SOURCE] == 22

    stored = [edge for edge in graph.edges if not edge.derived]
    assert len(stored) == seeded.count(Relation) == 147
    assert {edge.relation_id for edge in stored} == {rel.id for rel in seeded.find(Relation)}
    for edge in stored:  # stored type and direction are kept exactly
        relation = seeded.get(Relation, edge.relation_id)
        assert (edge.from_id, edge.to_id, edge.edge_type.value) == (
            relation.from_id, relation.to_id, relation.relation_type.value,
        )  # fmt: skip


def test_derived_edges_follow_citations_and_outcomes(seeded: Repository) -> None:
    graph = graph_for(seeded, Role.RESTRICTED_EXEC)
    expected_supported = {
        (record.id, ref.source_id, EdgeType.SUPPORTED_BY)
        for record in [*seeded.find(Decision), *seeded.find(Outcome)]
        for ref in record.source_refs
    }
    expected_resulted = {
        (outcome.decision_id, outcome.id, EdgeType.RESULTED_IN) for outcome in seeded.find(Outcome)
    }
    derived = {key for key in edge_keys(graph) if key[2] in (EdgeType.SUPPORTED_BY, EdgeType.RESULTED_IN)}
    assert derived == expected_supported | expected_resulted
    assert edge_type_counts(graph)[EdgeType.RESULTED_IN] == 14
    assert all(edge.relation_id is None for edge in graph.edges if edge.derived)


def test_edges_are_typed_and_the_main_relation_types_are_present(seeded: Repository) -> None:
    counts = edge_type_counts(graph_for(seeded, Role.RESTRICTED_EXEC))
    assert set(counts) == set(EdgeType)
    assert counts[EdgeType.SUPERSEDES] == 2
    assert counts[EdgeType.CONTRADICTS] == 2
    assert counts[EdgeType.SIMILAR_TO] == 4
    assert counts[EdgeType.INFORMED_BY] == 4


@pytest.mark.parametrize("role", list(Role))
def test_no_dangling_edges_for_any_role(seeded: Repository, role: Role) -> None:
    graph = graph_for(seeded, role)
    nodes = set(graph.node_ids())
    assert graph.edges
    for edge in graph.edges:
        assert edge.from_id in nodes and edge.to_id in nodes
    projected = to_networkx(graph)
    assert projected.number_of_nodes() == len(graph.nodes)
    assert projected.number_of_edges() == len(graph.edges)


@pytest.mark.parametrize("role", list(Role))
def test_graph_is_deterministic(seeded: Repository, role: Role) -> None:
    assert graph_for(seeded, role) == graph_for(seeded, role)
    assert layout(graph_for(seeded, role)) == layout(graph_for(seeded, role))


def test_decision_nodes_carry_readable_labels(seeded: Repository) -> None:
    graph = graph_for(seeded, Role.PRICING_DIRECTOR)
    node = graph.node("DEC-004")
    decision = seeded.get(Decision, "DEC-004")
    assert node.kind is NodeKind.DECISION
    assert node.label == decision.title
    assert decision.market in node.detail
    assert graph.node("OUT-001").verdict is OutcomeVerdict.FAILED
    assert graph.node("OUT-001").label == "Outcome of DEC-001: failed"


# --- permissions ------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_graph_nodes_match_what_the_role_may_see(seeded: Repository, role: Role) -> None:
    view = MemoryView(seeded, role)
    graph = build_decision_graph(view)
    assert set(graph.node_ids(NodeKind.DECISION)) == {d.id for d in view.decisions()}
    assert set(graph.node_ids(NodeKind.OUTCOME)) == {o.id for o in view.outcomes()}
    entity_ids = {
        node.id for node in graph.nodes if node.kind not in (NodeKind.DECISION, NodeKind.OUTCOME, NodeKind.SOURCE)
    }
    assert entity_ids == {e.id for e in view.entities()}
    assert set(graph.node_ids(NodeKind.SOURCE)) <= {s.id for s in view.sources()}


@pytest.mark.parametrize("role", [Role.PRICING_DIRECTOR, Role.ANALYST])
def test_no_hidden_ids_labels_or_notes_in_the_graph(seeded: Repository, role: Role) -> None:
    graph = graph_for(seeded, role)
    projected = to_networkx(graph)
    dumped = "\n".join(
        [
            graph.model_dump_json(),
            repr(sorted(projected.nodes(data=True))),
            repr(sorted(projected.edges(keys=True, data=True), key=repr)),
        ]
    )
    for secret in HIDDEN[role]:
        assert secret not in dumped


def test_analyst_graph_drops_the_hidden_decision_and_its_edges(seeded: Repository) -> None:
    analyst = graph_for(seeded, Role.ANALYST)
    director = graph_for(seeded, Role.PRICING_DIRECTOR)
    assert ("DEC-016", "DEC-004", EdgeType.SIMILAR_TO) in edge_keys(director)
    assert analyst.node("DEC-016") is None
    assert analyst.node("DEC-004") is not None
    assert not analyst.edges_of("DEC-016")
    assert analyst.node("SRC-021") is None


def test_a_visible_source_cited_only_by_hidden_records_is_left_out(seeded: Repository) -> None:
    # SRC-020 is visible to the analyst but only DEC-016 (hidden from them) cites it.
    view = MemoryView(seeded, Role.ANALYST)
    assert view.source("SRC-020") is not None
    assert graph_for(seeded, Role.ANALYST).node("SRC-020") is None
    assert graph_for(seeded, Role.PRICING_DIRECTOR).node("SRC-020") is not None


def test_hidden_and_missing_focus_give_the_same_empty_graph(seeded: Repository) -> None:
    director = graph_for(seeded, Role.PRICING_DIRECTOR)
    assert neighbourhood(director, "DEC-015") == neighbourhood(director, "DEC-999")
    assert neighbourhood(director, "DEC-015").nodes == []


# --- it is a projection: changes in SQLite show up on the next build --------


def test_new_outcome_changes_the_projection(seeded: Repository) -> None:
    before = graph_for(seeded, Role.PRICING_DIRECTOR)
    analyst_before = graph_for(seeded, Role.ANALYST)
    add_outcome(seeded, "OUT-900", "DEC-016", "SRC-020", "PRC-260")

    after = graph_for(seeded, Role.PRICING_DIRECTOR)
    assert before.node("OUT-900") is None
    assert after.node("OUT-900").verdict is OutcomeVerdict.PARTIAL
    assert edge_keys(after) - edge_keys(before) == {
        ("DEC-016", "OUT-900", EdgeType.RESULTED_IN),
        ("OUT-900", "SRC-020", EdgeType.SUPPORTED_BY),
    }
    # The analyst cannot see DEC-016, so neither the outcome nor its edges reach them.
    assert graph_for(seeded, Role.ANALYST) == analyst_before


def test_updated_outcome_verdict_changes_the_projection(seeded: Repository) -> None:
    outcome = seeded.get(Outcome, "OUT-004")
    assert graph_for(seeded, Role.ANALYST).node("OUT-004").verdict is OutcomeVerdict.SUCCESS
    seeded.save(outcome.model_copy(update={"verdict": OutcomeVerdict.FAILED}))
    node = graph_for(seeded, Role.ANALYST).node("OUT-004")
    assert node.verdict is OutcomeVerdict.FAILED
    assert node.label.endswith("failed")


def test_only_confirmed_decisions_enter_the_graph(seeded: Repository) -> None:
    source = seeded.get(SourceDocument, "SRC-001")
    ref = source.excerpt_ref("Then we need a review after the first full month.")
    seeded.add(make_decision(source, "DEC-901", status=DecisionStatus.DRAFT, source_refs=[ref]))
    assert graph_for(seeded, Role.RESTRICTED_EXEC).node("DEC-901") is None
    draft = seeded.get(Decision, "DEC-901")
    seeded.save(draft.model_copy(update={"status": DecisionStatus.CONFIRMED}))
    graph = graph_for(seeded, Role.RESTRICTED_EXEC)
    assert graph.node("DEC-901") is not None
    assert ("DEC-901", "SRC-001", EdgeType.SUPPORTED_BY) in edge_keys(graph)


# --- integrity --------------------------------------------------------------


def test_a_graph_with_a_dangling_edge_cannot_be_built() -> None:
    node = GraphNode(id="DEC-001", kind=NodeKind.DECISION, label="A decision")
    edge = GraphEdge(from_id="DEC-001", to_id="SRC-404", edge_type=EdgeType.SUPPORTED_BY)
    with pytest.raises(ValidationError, match="Dangling"):
        DecisionGraph(role=Role.ANALYST, nodes=[node], edges=[edge])
    with pytest.raises(ValidationError, match="unique"):
        DecisionGraph(role=Role.ANALYST, nodes=[node, node])


def test_stored_and_derived_edges_are_told_apart() -> None:
    with pytest.raises(ValidationError, match="derived"):
        GraphEdge(from_id="DEC-1", to_id="SRC-1", edge_type=EdgeType.SUPPORTED_BY, relation_id="REL-001")
    with pytest.raises(ValidationError, match="stored relation"):
        GraphEdge(from_id="DEC-1", to_id="DEC-2", edge_type=EdgeType.SUPERSEDES)
    with pytest.raises(ValidationError, match="itself"):
        GraphEdge(from_id="DEC-1", to_id="DEC-1", edge_type=EdgeType.SUPERSEDES, relation_id="REL-001")


def test_inconsistent_inputs_fail_loudly_with_a_neutral_message(
    seeded: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = MemoryView(seeded, Role.PRICING_DIRECTOR)
    monkeypatch.setattr(view, "sources", lambda **_: [])  # citations would now dangle
    with pytest.raises(GraphIntegrityError) as caught:
        build_decision_graph(view)
    assert "SRC-" not in str(caught.value) and "DEC-" not in str(caught.value)
    assert "reset_demo.py" in str(caught.value)


def test_empty_memory_gives_an_empty_graph(repo: Repository) -> None:
    graph = graph_for(repo, Role.ANALYST)
    assert graph.nodes == [] and graph.edges == []
    assert layout(graph) == {}


# --- views on a built graph -------------------------------------------------


def test_neighbourhood_of_the_france_precedent(seeded: Repository) -> None:
    director = neighbourhood(graph_for(seeded, Role.PRICING_DIRECTOR), "DEC-004")
    analyst = neighbourhood(graph_for(seeded, Role.ANALYST), "DEC-004")
    near = {"DEC-007", "DEC-008", "DEC-009", "DEC-014", "OUT-004"}
    assert near <= set(director.node_ids()) and near <= set(analyst.node_ids())
    assert "DEC-016" in director.node_ids() and "DEC-016" not in analyst.node_ids()
    assert "DEC-012" not in director.node_ids()  # two hops away, via DEC-001
    assert "DEC-012" in neighbourhood(graph_for(seeded, Role.PRICING_DIRECTOR), "DEC-001").node_ids()


def test_filter_kinds_keeps_only_edges_between_kept_nodes(seeded: Repository) -> None:
    graph = filter_kinds(graph_for(seeded, Role.PRICING_DIRECTOR), [NodeKind.DECISION])
    assert {node.kind for node in graph.nodes} == {NodeKind.DECISION}
    assert {edge.edge_type for edge in graph.edges} == {
        EdgeType.SUPERSEDES, EdgeType.CONTRADICTS, EdgeType.SIMILAR_TO, EdgeType.INFORMED_BY,
    }  # fmt: skip
    assert filter_kinds(graph, []).nodes == []


def test_networkx_projection_keys_edges_by_type(seeded: Repository) -> None:
    projected = to_networkx(graph_for(seeded, Role.PRICING_DIRECTOR))
    assert isinstance(projected, nx.MultiDiGraph)
    assert projected.has_edge("DEC-002", "DEC-001", key="SUPERSEDES")
    assert projected.nodes["DEC-004"]["kind"] == "decision"
    assert projected.graph["role"] == "pricing_director"


def test_layout_places_every_node(seeded: Repository) -> None:
    graph = graph_for(seeded, Role.ANALYST)
    positions = layout(graph)
    assert set(positions) == set(graph.node_ids())
    assert all(isinstance(value, float) for point in positions.values() for value in point)
