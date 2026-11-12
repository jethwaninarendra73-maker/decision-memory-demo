"""Agent 1 view: transcript -> extracted card -> human review -> confirm and save.

The transcript is shown with `st.text` and in data tables, both of which display text
literally. It is never rendered as Markdown or HTML, and nothing in it is run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from decision_memory.agents.capture_agent import (
    ALLOWED_SUFFIXES,
    CaptureError,
    TranscriptError,
    confirm_capture,
    extract_decisions,
    normalise_transcript,
    read_transcript_upload,
)
from decision_memory.audit import PermissionDeniedError
from decision_memory.authz import ROLE_LABELS, MemoryView
from decision_memory.models import (
    CaptureResult,
    DecisionDraft,
    DraftField,
    EntityKind,
    FlagKind,
    ReviewedDecision,
    Role,
)
from decision_memory.repository import Repository
from decision_memory.ui.evidence import SELECT_KEY as EVIDENCE_SELECT_KEY

DEMO_TRANSCRIPT = Path("capture") / "demo_transcript.md"
SOURCES = ("Demo transcript", "Paste text", "Upload .txt or .md")

STATE_KEY = "capture_state"  # {"text": str, "result": CaptureResult}
NOTICE_KEY = "capture_notice"  # (kind, message) shown once after confirm or reject
FIELD_LABELS: dict[DraftField, str] = {
    DraftField.STATEMENT: "What was decided",
    DraftField.DECISION_DATE: "Decision date",
    DraftField.OWNER: "Owner",
    DraftField.STAKEHOLDERS: "Stakeholders",
    DraftField.REGION: "Region",
    DraftField.MARKET: "Market",
    DraftField.CUSTOMER_SEGMENT: "Customer segment",
    DraftField.SURCHARGE_RATE: "Surcharge rate",
    DraftField.RATIONALE: "Rationale",
    DraftField.ALTERNATIVES: "Alternatives considered",
    DraftField.RISKS: "Key risks",
    DraftField.EXPECTED_OUTCOME: "Expected outcome",
}
REVIEW_LABELS = {"title": "Short title", **{f.value: label for f, label in FIELD_LABELS.items()}}


def _key(name: str) -> str:
    return f"cap_{name}"


def _display(draft: DecisionDraft, field: DraftField) -> str:
    value = getattr(draft, field.value)
    if value in (None, "", []):
        return "—"
    if field is DraftField.SURCHARGE_RATE:
        return f"{value:.1%}"
    if isinstance(value, list):
        return "; ".join(value)
    return str(value)


def _load_transcript(choice: str, fixtures_dir: Path) -> str | None:
    """Return the chosen transcript as plain text, or None (after showing why)."""
    try:
        if choice == SOURCES[0]:
            return normalise_transcript((fixtures_dir / DEMO_TRANSCRIPT).read_text(encoding="utf-8"))
        if choice == SOURCES[1]:
            pasted = st.text_area(
                "Paste a synthetic transcript",
                key="capture_paste",
                height=200,
                placeholder="Meeting: ...\nDate: YYYY-MM-DD\nAttendees: ...\n\nDECISION: ...\nOWNER: ...",
            )
            return normalise_transcript(pasted) if pasted.strip() else None
        uploaded = st.file_uploader(
            "Upload a synthetic transcript",
            type=[suffix.lstrip(".") for suffix in ALLOWED_SUFFIXES],
            key="capture_upload",
        )
        if uploaded is None:
            return None
        return read_transcript_upload(uploaded.name, uploaded.getvalue())
    except FileNotFoundError:
        st.error("The demo transcript fixture is missing from data/fixtures/capture/.")
    except TranscriptError as exc:
        st.error(str(exc))
    return None


def _prefill_review(draft: DecisionDraft, role: Role) -> None:
    """Load the extracted values into the review form. Missing fields stay blank."""
    state = st.session_state
    state[_key("title")] = draft.title
    state[_key("statement")] = draft.statement
    state[_key("decision_date")] = draft.decision_date.isoformat() if draft.decision_date else ""
    state[_key("owner")] = draft.owner or ""
    state[_key("stakeholders")] = ", ".join(draft.stakeholders)
    state[_key("region")] = draft.region or ""
    state[_key("market")] = draft.market or ""
    state[_key("customer_segment")] = draft.customer_segment or ""
    state[_key("surcharge_rate")] = (
        "" if draft.surcharge_rate is None else f"{draft.surcharge_rate * 100:g}"
    )
    state[_key("rationale")] = draft.rationale
    state[_key("alternatives")] = "\n".join(draft.alternatives)
    state[_key("risks")] = "\n".join(draft.risks)
    state[_key("expected_outcome")] = draft.expected_outcome
    state[_key("access_roles")] = [role.value for role in Role]
    state[_key("actor")] = f"Demo {ROLE_LABELS[role]}"


def _render_card(draft: DecisionDraft) -> None:
    with st.container(border=True):
        st.markdown("**Extracted decision card** · draft, not yet in memory")
        st.text(draft.statement)
        st.markdown(
            f"**Extraction confidence:** {draft.confidence_label} ({draft.confidence:.2f}) · "
            "rule-based on how explicitly each field is stated; not a model prediction"
        )
        flags = {flag.field: flag for flag in draft.flags}
        rows = []
        for field, label in FIELD_LABELS.items():
            spans = [span for span in draft.evidence if span.field is field]
            flag = flags.get(field)
            rows.append(
                {
                    "Field": label,
                    "Extracted value": _display(draft, field),
                    "Confidence": f"{draft.field_confidence.get(field, 0.0):.1f}",
                    "Evidence offsets": ", ".join(
                        f"{span.start_offset}–{span.end_offset}" for span in spans
                    )
                    or "—",
                    "Flag": f"{flag.kind.value}: {flag.reason}" if flag else "",
                }
            )
        st.dataframe(rows, hide_index=True)
        for flag in draft.flags:
            show = st.error if flag.kind is FlagKind.MISSING else st.warning
            show(f"{FIELD_LABELS[flag.field]}: {flag.reason}")
        if draft.blocking_fields:
            names = ", ".join(FIELD_LABELS[field] for field in draft.blocking_fields)
            st.info(f"Not guessed. A reviewer must fill in: {names}.")


def _render_not_decisions(result: CaptureResult) -> None:
    count = len(result.not_decisions)
    with st.expander(f"Not treated as decisions ({count})", expanded=count > 0):
        if not count:
            st.text("Nothing else in the transcript looked like a decision.")
            return
        st.dataframe(
            [{"Transcript text": item.excerpt, "Why not": item.reason} for item in result.not_decisions],
            hide_index=True,
        )


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _reviewed_from_form() -> ReviewedDecision | None:
    """Validate the review form. Shows what to fix and returns None if it is incomplete."""
    state = st.session_state
    rate_text = state[_key("surcharge_rate")].strip().rstrip("%")
    try:
        rate = float(rate_text) / 100 if rate_text else None
    except ValueError:
        st.error("Surcharge rate must be a number, for example 5 for 5%.")
        return None
    try:
        return ReviewedDecision(
            title=state[_key("title")],
            statement=state[_key("statement")],
            decision_date=state[_key("decision_date")].strip() or None,
            owner=state[_key("owner")],
            stakeholders=[n.strip() for n in state[_key("stakeholders")].split(",") if n.strip()],
            region=state[_key("region")],
            market=state[_key("market")],
            customer_segment=state[_key("customer_segment")],
            surcharge_rate=rate,
            rationale=state[_key("rationale")],
            alternatives=_lines(state[_key("alternatives")]),
            risks=_lines(state[_key("risks")]),
            expected_outcome=state[_key("expected_outcome")],
        )
    except ValidationError as exc:
        fields = dict.fromkeys(str(error["loc"][0]) for error in exc.errors())
        names = ", ".join(REVIEW_LABELS.get(name, name) for name in fields)
        st.error(f"Cannot confirm yet. Please complete or correct: {names}.")
        return None


def _render_review(repo: Repository, view: MemoryView, text: str, result: CaptureResult) -> None:
    draft = result.drafts[0]
    segments = ", ".join(entity.name for entity in view.entities(kind=EntityKind.SEGMENT))
    with st.form("capture_review"):
        st.markdown("**Review** · edit anything, then confirm or reject")
        st.text_input(REVIEW_LABELS["title"], key=_key("title"))
        st.text_area(REVIEW_LABELS["statement"], key=_key("statement"), height=80)
        left, right = st.columns(2)
        with left:
            st.text_input("Decision date (YYYY-MM-DD)", key=_key("decision_date"))
            st.text_input(REVIEW_LABELS["owner"], key=_key("owner"))
            st.text_input("Stakeholders (comma-separated)", key=_key("stakeholders"))
        with right:
            st.text_input(REVIEW_LABELS["region"], key=_key("region"))
            st.text_input(REVIEW_LABELS["market"], key=_key("market"))
            st.text_input(
                REVIEW_LABELS["customer_segment"],
                key=_key("customer_segment"),
                help=f"Segments already in memory: {segments}" if segments else None,
            )
        st.text_input("Surcharge rate in % (optional)", key=_key("surcharge_rate"))
        st.text_area(REVIEW_LABELS["rationale"], key=_key("rationale"), height=80)
        st.text_area("Alternatives considered (one per line)", key=_key("alternatives"), height=70)
        st.text_area("Key risks (one per line)", key=_key("risks"), height=80)
        st.text_area(REVIEW_LABELS["expected_outcome"], key=_key("expected_outcome"), height=70)
        st.multiselect(
            "Demo roles that may see this source",
            options=[role.value for role in Role],
            format_func=lambda value: ROLE_LABELS[Role(value)],
            key=_key("access_roles"),
        )
        st.text_input("Confirmed by (demo label)", key=_key("actor"))
        confirm_column, reject_column = st.columns(2)
        with confirm_column:
            confirm = st.form_submit_button("Confirm and save to memory", type="primary")
        with reject_column:
            reject = st.form_submit_button("Reject draft")

    if reject:
        st.session_state.pop(STATE_KEY, None)
        st.session_state[NOTICE_KEY] = ("info", "Draft rejected. Nothing was saved.")
        st.rerun()
    if not confirm:
        return

    reviewed = _reviewed_from_form()
    actor = st.session_state[_key("actor")].strip()
    if reviewed is None:
        return
    if not actor:
        st.error("Enter a name or label for who is confirming this decision.")
        return
    try:
        outcome = confirm_capture(
            repo,
            transcript=text,
            draft=draft,
            reviewed=reviewed,
            meeting_title=result.meeting_title,
            access_roles=[Role(value) for value in st.session_state[_key("access_roles")]],
            actor_label=actor,
            role=view.role,
            now=datetime.now(UTC),
        )
    except (CaptureError, PermissionDeniedError) as exc:
        st.error(str(exc))
        return

    st.session_state.pop(STATE_KEY, None)
    st.session_state[EVIDENCE_SELECT_KEY] = outcome.source.id
    st.session_state[NOTICE_KEY] = (
        "success",
        f"Confirmed. {outcome.decision.id} is now in institutional memory, confirmed by "
        f"{actor} ({ROLE_LABELS[view.role]}). Its transcript is stored as {outcome.source.id} "
        f"with {len(outcome.decision.source_refs)} evidence excerpts, and the confirmation "
        "is in the audit trail. The source is open in the Evidence explorer below.",
    )
    st.rerun()


def render_capture(repo: Repository, view: MemoryView, fixtures_dir: Path) -> None:
    """The whole Agent 1 tab."""
    st.subheader("Agent 1 · Decision Capture")
    st.caption(
        "Reads a synthetic meeting transcript as plain text and proposes a decision for a "
        "human to confirm. Nothing in the text is run, sent anywhere or followed as an "
        "instruction, and this demo is not connected to Teams."
    )
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        (st.success if notice[0] == "success" else st.info)(notice[1])

    choice = st.radio("Transcript", SOURCES, horizontal=True, key="capture_source")
    text = _load_transcript(choice, fixtures_dir)
    if text:
        with st.container(height=260):
            st.text(text)

    if st.button("Extract decision", key="capture_extract", type="primary", disabled=not text):
        markets = {entity.name: entity.description for entity in view.entities(kind=EntityKind.MARKET)}
        result = extract_decisions(text, known_markets=markets)
        st.session_state[STATE_KEY] = {"text": text, "result": result}
        if result.drafts:
            _prefill_review(result.drafts[0], view.role)

    state = st.session_state.get(STATE_KEY)
    if not state or state["text"] != text:
        # Nothing extracted yet, or the transcript changed since the last extraction.
        st.session_state.pop(STATE_KEY, None)
        return

    result: CaptureResult = state["result"]
    for warning in result.warnings:
        st.warning(warning)
    if result.drafts:
        if len(result.drafts) > 1:
            st.info(
                f"{len(result.drafts)} DECISION lines were found. This view reviews the first; "
                "capture the others from separate transcripts."
            )
        _render_card(result.drafts[0])
    _render_not_decisions(result)
    if result.drafts:
        _render_review(repo, view, text, result)
