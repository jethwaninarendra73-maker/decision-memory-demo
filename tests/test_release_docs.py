"""The release documents (Stage 15) stay true to the repository: the scorecard covers every
acceptance criterion word for word, every test the documents cite exists, and every link
between documents resolves."""

from __future__ import annotations

import re

import pytest

from decision_memory import SYNTHETIC_BANNER

from conftest import REPO_ROOT

DOCS = REPO_ROOT / "docs"
SPEC = (DOCS / "PRODUCT_SPEC.md").read_text(encoding="utf-8")
CRITERIA = SPEC.split("## 6. Acceptance criteria")[1]
RELEASE_DOCS = ("RELEASE_SCORECARD.md", "HANDOFF.md", "QA_REPORT.md")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
TEST_NAME = re.compile(r"\btest_[a-z0-9_]+\b")
DEFINED_TESTS = {
    name
    for path in (REPO_ROOT / "tests").glob("test_*.py")
    for name in re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)
}
TEST_MODULES = {path.stem for path in (REPO_ROOT / "tests").glob("test_*.py")}


def criteria() -> list[str]:
    return re.findall(r"^- \[[ x]\] (.+)$", CRITERIA, re.MULTILINE)


def test_the_scorecard_lists_every_acceptance_criterion_verbatim() -> None:
    scorecard = (DOCS / "RELEASE_SCORECARD.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\d+) \| (.+?) \| .+? \| (\w+) \|", scorecard, re.MULTILINE)
    assert len(criteria()) == 28
    assert [text for _, text, _ in rows] == [c.replace("|", "/") for c in criteria()]
    assert [number for number, _, _ in rows] == [str(n) for n in range(1, 29)]
    assert {result for _, _, result in rows} == {"Pass"}


def test_a_criterion_is_ticked_only_with_a_scorecard_behind_it() -> None:
    ticked = re.findall(r"^- \[x\] ", CRITERIA, re.MULTILINE)
    if ticked:
        assert len(ticked) == len(criteria())  # all or nothing: the gate is all of them
        assert "[RELEASE_SCORECARD.md](RELEASE_SCORECARD.md)" in CRITERIA


@pytest.mark.parametrize("name", RELEASE_DOCS)
def test_every_test_a_release_document_cites_exists(name: str) -> None:
    text = (DOCS / name).read_text(encoding="utf-8")
    cited = set(TEST_NAME.findall(text))
    assert cited, name
    unknown = sorted(cited - DEFINED_TESTS - TEST_MODULES)
    assert not unknown, unknown


@pytest.mark.parametrize("name", RELEASE_DOCS)
def test_release_documents_link_only_to_files_that_exist(name: str) -> None:
    text = (DOCS / name).read_text(encoding="utf-8")
    assert SYNTHETIC_BANNER in text or name == "QA_REPORT.md"
    for target in LINK.findall(text):
        if not target.startswith(("http://", "https://")):
            assert (DOCS / target).resolve().exists(), (name, target)


def test_the_handoff_names_files_that_exist() -> None:
    handoff = (DOCS / "HANDOFF.md").read_text(encoding="utf-8")
    for path in re.findall(r"`((?:tests|data|scripts|src)/[\w./-]+|[\w]+\.py)`", handoff):
        candidates = [REPO_ROOT / path, *(REPO_ROOT / "src" / "decision_memory").rglob(path)]
        assert any(c.exists() for c in candidates), path
