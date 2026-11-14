"""Impact tab in the running app: labelled items, citations, abstention and role filtering."""

from __future__ import annotations

from datetime import date

import pytest

from decision_memory.repository import Repository
from decision_memory.ui.impact import (
    EXCLUDE_HVC_KEY,
    NO_SEARCH_MESSAGE,
    NOTHING_TO_ASSESS,
    REFUSED_MESSAGE,
)
from decision_memory.ui.search import AS_OF_KEY, QUESTION_KEY

from conftest import (
    HIDDEN_FROM_ANALYST,
    HIDDEN_FROM_DIRECTOR,
    make_decision,
    make_source,
    rendered_text,
)

AS_OF = date(2026, 11, 13)


def demo(app):
    app.date_input(key=AS_OF_KEY).set_value(AS_OF)
    return app.run()


def item_headers(app) -> list[str]:
    return [m.value for m in app.markdown if " · confidence " in m.value]


def impact_table(app):
    (table,) = [
        f.value for f in app.dataframe if "Basis" in f.value.columns and "Range" in f.value.columns
    ]
    return table


def test_demo_impact_lists_labelled_cited_items(run_app) -> None:
    app = demo(run_app())
    assert not app.exception and not app.error
    text = rendered_text(app)
    assert "18 impact item(s) from 5 precedent(s): 16 source-supported, 2 assumed for demo." in text
    assert "effective 2027-Q1" in text
    headers = item_headers(app)
    assert len(headers) == 18
    assert all("Source-supported" in h or "Assumed for demo" in h for h in headers)
    assert "**High-volume contract customers** · Source-supported · confidence high" in headers
    assert "**Competitive response in Germany** · Source-supported · confidence medium" in headers
    table = impact_table(app)
    assert len(table) == 18 and set(table["Basis"]) == {"Source-supported", "Assumed for demo"}
    assumed = table[table["Basis"] == "Assumed for demo"]
    assert list(assumed["Item"]) == ["One-off implementation cost", "Implementation effort"]
    assert "$0.20M – $0.35M" in text and "7 – 12 person-weeks" in text
    # Quoted evidence links back to the local evidence view and is escaped Markdown.
    assert "[\\[SRC-002\\]](?role=pricing_director&evidence=SRC-002)" in text
    assert any(
        w.value.startswith("Seen in precedent(s) that did not go to plan") and "DEC-001" in w.value
        for w in app.warning
    )


def test_off_topic_question_makes_impact_abstain(run_app) -> None:
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(
        "Should we migrate the payroll system to a new vendor?"
    )
    app = app.run()
    assert not app.exception
    assert any(
        w.value.startswith("Insufficient verifiable evidence. There are no precedents")
        for w in app.warning
    )
    assert not item_headers(app)


def test_excluding_contract_customers_is_shown_on_the_group(run_app) -> None:
    app = demo(run_app())
    assert "Not excluded by this proposal." in rendered_text(app)
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    text = rendered_text(app)
    assert "Excluded by this proposal." in text
    assert "exclusions: High-volume contract customers" in text


@pytest.mark.parametrize(
    ("role", "hidden"),
    [("pricing_director", HIDDEN_FROM_DIRECTOR), ("analyst", HIDDEN_FROM_ANALYST)],
)
def test_impact_tab_shows_nothing_hidden(run_app, role: str, hidden) -> None:
    app = demo(run_app(role=role))
    on_screen = rendered_text(app)
    for secret in hidden:
        assert secret not in on_screen


def test_restricted_question_assesses_only_for_the_role_that_can_see_it(run_app) -> None:
    question = "Board strategy: hold fixed pricing for the twenty largest strategic accounts?"
    director = run_app(role="pricing_director")
    director.text_input(key=QUESTION_KEY).set_value(question)
    director = director.run()
    assert not item_headers(director)
    exec_app = run_app(role="restricted_exec")
    exec_app.text_input(key=QUESTION_KEY).set_value(question)
    exec_app = exec_app.run()
    assert "**Competitive response in Germany** · Source-supported · confidence low" in (
        item_headers(exec_app)
    )


def test_empty_memory_says_there_is_nothing_to_assess(run_app, tmp_path) -> None:
    empty = tmp_path / "empty.db"
    Repository.open(empty).close()
    app = run_app(db=empty)
    assert not app.exception
    text = rendered_text(app)
    assert NOTHING_TO_ASSESS in text and NO_SEARCH_MESSAGE not in text


def test_empty_question_asks_for_a_search_first(run_app) -> None:
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value("   ")
    app = app.run()
    assert not app.exception
    assert NO_SEARCH_MESSAGE in rendered_text(app)


def test_padded_bullet_in_a_captured_source_is_quoted_not_a_crash(run_app, tmp_path) -> None:
    # Regression: "-  Billing" (two spaces) used to give offsets one character off, and
    # the refused citation crashed the whole page.
    db = tmp_path / "bullet.db"
    statement = "Apply a 2% test surcharge in Testland for European customers."
    body = f"Intro line.\nDECISION: {statement}\n-  Billing  tables need an update.\n"
    with Repository.open(db) as repo:
        source = repo.add(make_source(body=body, region="Europe"))
        repo.add(make_decision(source, statement=statement,
                               source_refs=[source.excerpt_ref(statement)]))  # fmt: skip
    app = run_app(role="analyst", db=db)
    app.text_input(key=QUESTION_KEY).set_value("Should we apply a 2% surcharge in Testland?")
    app = app.run()
    assert not app.exception
    assert "**Billing and rate tables** · Source-supported · confidence low" in item_headers(app)
    assert "“Billing  tables need an update\\.”" in rendered_text(app)  # escaped, exact
    assert "Evidence explorer" in [s.value for s in app.subheader]  # the page kept rendering


def test_a_refused_assessment_is_withheld_and_the_page_keeps_running(run_app, monkeypatch) -> None:
    from decision_memory.provenance import InaccessibleEvidenceError

    def refuse(view, assessment) -> None:
        raise InaccessibleEvidenceError("refused")

    monkeypatch.setattr("decision_memory.ui.impact.verify_assessment", refuse)
    app = demo(run_app())
    assert not app.exception
    assert REFUSED_MESSAGE in [w.value for w in app.warning]
    assert not item_headers(app)
    assert "Evidence explorer" in [s.value for s in app.subheader]


def test_evidence_says_which_record_cites_the_quoted_source(run_app) -> None:
    text = rendered_text(demo(run_app()))
    assert "- DEC-001, outcome (failed) source: [\\[SRC-002\\]]" in text
    assert "- DEC-009, decision and outcome (partial) source: [\\[SRC-013\\]]" in text
    assert "- DEC-004, decision source: [\\[SRC-006\\]]" in text
