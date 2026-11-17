"""Agent 2 — Institutional Memory / Decision Graph.

The graph is a projection of SQLite, not a second store. `build_decision_graph` reads
through a `MemoryView`, so it only ever sees records the active role may see, and it is
rebuilt from the database on every call: a new or updated outcome shows up the next time
the graph is built, with nothing to invalidate.

What goes in:
- every visible confirmed decision, outcome and entity;
- the sources cited by those decisions and outcomes, or named by a visible relation
  (a visible source that nothing visible cites is left out, so the graph does not hint
  at hidden records that might cite it);
- stored relations whose two ends are visible, with their stored type;
- derived edges: `SUPPORTED_BY` from a decision or outcome to each source it cites,
  and `RESULTED_IN` from a decision to each of its outcomes.

`DecisionGraph` refuses to exist with a dangling edge, so a projection that would point
at a missing or hidden node fails loudly instead of quietly drawing it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import networkx as nx

from ..authz import MemoryView
from ..models import (
    Decision,
    DecisionGraph,
    EdgeType,
    Entity,
    GraphEdge,
    GraphNode,
    NodeKind,
    Outcome,
    SourceDocument,
    SourceRef,
)
from ..provenance import SOURCE_TYPE_LABELS
from .base import AgentInfo

logger = logging.getLogger(__name__)

INFO = AgentInfo(
    number=2,
    key="memory",
    name="Institutional Memory",
    tab_name="Past decisions",
    purpose="Connects confirmed decisions, sources and outcomes into one decision graph.",
    reads="Confirmed decisions, sources and outcomes the active role may see",
    produces="Graph of decisions, markets, segments, systems, risks, stakeholders and outcomes",
    build_stage=5,
)

# Display order of node kinds: decisions first, evidence last.
KIND_ORDER: tuple[NodeKind, ...] = (
    NodeKind.DECISION,
    NodeKind.OUTCOME,
    NodeKind.MARKET,
    NodeKind.SEGMENT,
    NodeKind.CUSTOMER_GROUP,
    NodeKind.SYSTEM,
    NodeKind.RISK,
    NodeKind.INITIATIVE,
    NodeKind.STAKEHOLDER,
    NodeKind.SOURCE,
)
_KIND_RANK = {kind: rank for rank, kind in enumerate(KIND_ORDER)}
LAYOUT_SEED = 7


class GraphIntegrityError(Exception):
    """The projection would contain a dangling or duplicate edge."""


# --- node builders ----------------------------------------------------------


def _decision_node(decision: Decision) -> GraphNode:
    rate = "" if decision.surcharge_rate is None else f" · surcharge {decision.surcharge_rate:.1%}"
    return GraphNode(
        id=decision.id,
        kind=NodeKind.DECISION,
        label=decision.title,
        detail=(
            f"{decision.decision_date.isoformat()} · {decision.market} · "
            f"{decision.customer_segment}{rate}"
        ),
        node_date=decision.decision_date,
        region=decision.region,
    )


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}"


def _outcome_node(outcome: Outcome) -> GraphNode:
    return GraphNode(
        id=outcome.id,
        kind=NodeKind.OUTCOME,
        label=f"Outcome of {outcome.decision_id}: {outcome.verdict.value}",
        detail=(
            f"observed {outcome.observed_on.isoformat()} · revenue change USD M "
            f"expected {_money(outcome.expected_revenue_change_musd)}, "
            f"actual {_money(outcome.actual_revenue_change_musd)}"
        ),
        node_date=outcome.observed_on,
        verdict=outcome.verdict,
    )


def _entity_node(entity: Entity) -> GraphNode:
    return GraphNode(
        id=entity.id,
        kind=NodeKind(entity.kind.value),
        label=entity.name,
        detail=entity.description,
    )


def _source_node(source: SourceDocument) -> GraphNode:
    return GraphNode(
        id=source.id,
        kind=NodeKind.SOURCE,
        label=source.title,
        detail=(
            f"{SOURCE_TYPE_LABELS[source.source_type]} (synthetic) · "
            f"{source.document_date.isoformat()}"
        ),
        node_date=source.document_date,
        region=source.region,
    )


# --- edges ------------------------------------------------------------------


def _citation_edges(owner_id: str, refs: Iterable[SourceRef]) -> list[GraphEdge]:
    """One `SUPPORTED_BY` edge per distinct cited source, noting how many excerpts."""
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.source_id] = counts.get(ref.source_id, 0) + 1
    return [
        GraphEdge(
            from_id=owner_id,
            to_id=source_id,
            edge_type=EdgeType.SUPPORTED_BY,
            note=f"{count} cited excerpt{'s' if count != 1 else ''}",
        )
        for source_id, count in counts.items()
    ]


# --- projection -------------------------------------------------------------


def build_decision_graph(view: MemoryView) -> DecisionGraph:
    """Project the records `view` may see into a typed decision graph.

    Deterministic: the same database and role always give the same graph, with nodes
    ordered by kind then ID, and edges by endpoints then type.
    """
    decisions = view.decisions()
    outcomes = view.outcomes()
    entities = view.entities()
    relations = view.relations()

    edges: list[GraphEdge] = []
    for decision in decisions:
        edges += _citation_edges(decision.id, decision.source_refs)
    for outcome in outcomes:
        edges.append(
            GraphEdge(
                from_id=outcome.decision_id,
                to_id=outcome.id,
                edge_type=EdgeType.RESULTED_IN,
                note=outcome.verdict.value,
            )
        )
        edges += _citation_edges(outcome.id, outcome.source_refs)
    edges += [
        GraphEdge(
            from_id=relation.from_id,
            to_id=relation.to_id,
            edge_type=EdgeType(relation.relation_type.value),
            relation_id=relation.id,
            note=relation.note,
        )
        for relation in relations
    ]

    needed_sources = {
        end for edge in edges for end in (edge.from_id, edge.to_id) if end.startswith("SRC-")
    }
    nodes = [
        *(_decision_node(d) for d in decisions),
        *(_outcome_node(o) for o in outcomes),
        *(_entity_node(e) for e in entities),
        *(_source_node(s) for s in view.sources() if s.id in needed_sources),
    ]
    nodes.sort(key=lambda node: (_KIND_RANK[node.kind], node.id))
    edges.sort(key=lambda edge: (edge.from_id, edge.to_id, edge.edge_type.value))

    try:
        graph = DecisionGraph(role=view.role, nodes=nodes, edges=edges)
    except ValueError as exc:
        # Report the rule, not record content: the message may be shown in the UI.
        logger.error("Decision graph projection failed its integrity check")
        raise GraphIntegrityError(
            "The decision graph could not be built consistently from the database. "
            "Run `python scripts/reset_demo.py` to restore the demo baseline."
        ) from exc
    logger.debug("Built decision graph: %d nodes, %d edges", len(nodes), len(edges))
    return graph


# --- views on a built graph -------------------------------------------------


def to_networkx(graph: DecisionGraph) -> nx.MultiDiGraph:
    """The graph as a NetworkX multigraph; edges are keyed by edge type."""
    projected = nx.MultiDiGraph(role=graph.role.value)
    for node in graph.nodes:
        projected.add_node(node.id, kind=node.kind.value, label=node.label)
    for edge in graph.edges:
        projected.add_edge(
            edge.from_id,
            edge.to_id,
            key=edge.edge_type.value,
            edge_type=edge.edge_type.value,
            derived=edge.derived,
            relation_id=edge.relation_id,
        )
    return projected


def _restrict(graph: DecisionGraph, keep: set[str]) -> DecisionGraph:
    return DecisionGraph(
        role=graph.role,
        nodes=[node for node in graph.nodes if node.id in keep],
        edges=[edge for edge in graph.edges if edge.from_id in keep and edge.to_id in keep],
    )


def filter_kinds(graph: DecisionGraph, kinds: Iterable[NodeKind]) -> DecisionGraph:
    """Keep only nodes of the given kinds, and the edges between them."""
    wanted = set(kinds)
    return _restrict(graph, {node.id for node in graph.nodes if node.kind in wanted})


def neighbourhood(graph: DecisionGraph, node_id: str, radius: int = 1) -> DecisionGraph:
    """Nodes within `radius` hops of `node_id`, ignoring edge direction.

    An unknown node gives an empty graph, the same as a node the role cannot see.
    """
    if graph.node(node_id) is None:
        return DecisionGraph(role=graph.role)
    reach = nx.single_source_shortest_path_length(
        to_networkx(graph).to_undirected(as_view=True), node_id, cutoff=radius
    )
    return _restrict(graph, set(reach))


def neighbourhoods(graph: DecisionGraph, node_ids: Iterable[str], radius: int = 1) -> DecisionGraph:
    """Union of the neighbourhoods of several nodes; unknown IDs contribute nothing."""
    keep: set[str] = set()
    for node_id in node_ids:
        keep |= set(neighbourhood(graph, node_id, radius).node_ids())
    return _restrict(graph, keep)


def layout(graph: DecisionGraph, seed: int = LAYOUT_SEED) -> dict[str, tuple[float, float]]:
    """Deterministic 2-D positions for drawing (spring layout with a fixed seed)."""
    if not graph.nodes:
        return {}
    simple = nx.Graph()
    simple.add_nodes_from(graph.node_ids())
    simple.add_edges_from((edge.from_id, edge.to_id) for edge in graph.edges)
    positions = nx.spring_layout(simple, seed=seed)
    return {node_id: (float(x), float(y)) for node_id, (x, y) in positions.items()}


def kind_counts(graph: DecisionGraph) -> dict[NodeKind, int]:
    """Node count per kind, in display order, omitting kinds with no nodes."""
    counts = {kind: len(graph.node_ids(kind)) for kind in KIND_ORDER}
    return {kind: count for kind, count in counts.items() if count}


def edge_type_counts(graph: DecisionGraph) -> dict[EdgeType, int]:
    """Edge count per type, in `EdgeType` order, omitting types with no edges."""
    counts = {
        edge_type: sum(1 for edge in graph.edges if edge.edge_type is edge_type)
        for edge_type in EdgeType
    }
    return {edge_type: count for edge_type, count in counts.items() if count}
