"""Safety regressions (Stage 13): the suite's own guards, no outbound code paths, no raw
HTML, and no secrets or generated data in the repository."""

from __future__ import annotations

import ast
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from decision_memory.config import load_settings

from conftest import OFFLINE_ENV, REPO_ROOT, NetworkBlockedError

CODE_DIRS = (REPO_ROOT / "src", REPO_ROOT / "scripts")
CODE_FILES = sorted(
    [REPO_ROOT / "app.py", *(f for d in CODE_DIRS for f in d.rglob("*.py"))]
)  # fmt: skip
# Modules that could send something out of this machine or run other programs.
OUTBOUND = {
    "socket", "smtplib", "ftplib", "telnetlib", "poplib", "imaplib", "http", "urllib",
    "requests", "httpx", "httpx2", "aiohttp", "websocket", "websockets", "subprocess",
    "webbrowser", "paramiko", "boto3", "openai",
}  # fmt: skip
# The only module allowed to import the optional Anthropic SDK.
LLM_MODULE = REPO_ROOT / "src" / "decision_memory" / "llm_gateway.py"
# Calls that run other programs or import modules by name, which an import scan misses.
OS_CALLS = {"system", "popen", "startfile"}
OS_CALL_PREFIXES = ("exec", "spawn", "posix_spawn")
DYNAMIC_IMPORTS = {"__import__", "import_module"}
# Key-shaped text: flagged in every file.
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
# A key assigned a value: flagged outside tests/, where tests write fake .env lines.
KEY_ASSIGNMENT = re.compile(r"ANTHROPIC_API_KEY\s*=\s*['\"]?[A-Za-z0-9_-]{12,}")
BINARY_SUFFIXES = {".db", ".png", ".jpg", ".docx"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def outbound_calls(path: Path) -> set[str]:
    """`os.system(...)`, `os.exec*(...)`, `__import__(...)`, `importlib.import_module(...)`
    and the like, by name."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "importlib"}:
            names = {alias.name for alias in node.names}
            found |= {n for n in names if n in OS_CALLS | DYNAMIC_IMPORTS}
            found |= {n for n in names if n.startswith(OS_CALL_PREFIXES)}
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in DYNAMIC_IMPORTS:
            found.add(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner, name = func.value.id, func.attr
            if owner == "os" and (name in OS_CALLS or name.startswith(OS_CALL_PREFIXES)):
                found.add(f"os.{name}")
            elif owner == "importlib" and name in DYNAMIC_IMPORTS:
                found.add(f"importlib.{name}")
    return found


def secret_findings(relative: str, text: str) -> list[str]:
    """The patterns `text` matches; the key-assignment rule applies outside tests/ only."""
    patterns = [*SECRET_PATTERNS]
    if not relative.startswith("tests/"):
        patterns.append(KEY_ASSIGNMENT)
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


def _git_files(*args: str) -> list[Path]:
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("git is not available")
    listed = subprocess.run(
        ["git", "ls-files", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [REPO_ROOT / name for name in listed]


def tracked_files() -> list[Path]:
    return _git_files()


def committable_files() -> list[Path]:
    """Tracked files plus new files git would pick up (not ignored), so a secret is caught
    before its first commit, not after."""
    return _git_files("--cached", "--others", "--exclude-standard")


# --- the suite's own guards ------------------------------------------------------------


def test_the_suite_cannot_reach_the_network() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_a_local_env_file_cannot_switch_the_tests_online(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODE=anthropic\nANTHROPIC_API_KEY=placeholder-not-a-key\n")
    settings = load_settings(env=OFFLINE_ENV, env_file=env_file)
    assert settings.effective_llm_mode == "offline"
    assert settings.anthropic_api_key is None


# --- no outbound code paths ------------------------------------------------------------


@pytest.mark.parametrize("path", CODE_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_code_imports_a_way_out(path: Path) -> None:
    imports = imported_modules(path)
    assert not imports & OUTBOUND, sorted(imports & OUTBOUND)
    if path != LLM_MODULE:
        assert "anthropic" not in imports


@pytest.mark.parametrize("path", CODE_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_code_runs_programs_or_imports_by_name(path: Path) -> None:
    assert not outbound_calls(path), sorted(outbound_calls(path))


def test_the_call_scan_catches_what_an_import_scan_misses(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os, importlib\n"
        "from os import execv\n"
        "os.system('x'); os.popen('x'); os.execvp('x', []); os.spawnl(0, 'x')\n"
        "__import__('smtplib'); importlib.import_module('socket')\n"
        "importlib.util.find_spec('anthropic')\n"  # a presence check is fine
    )
    assert outbound_calls(sample) == {
        "execv", "os.system", "os.popen", "os.execvp", "os.spawnl", "__import__",
        "importlib.import_module",
    }  # fmt: skip


def test_the_llm_gateway_imports_the_sdk_only_inside_functions() -> None:
    # A module-level import would make the optional package required.
    tree = ast.parse(LLM_MODULE.read_text(encoding="utf-8"))
    top_level = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert "anthropic" not in top_level


@pytest.mark.parametrize("path", CODE_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_raw_html_rendering(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for forbidden in ("unsafe_allow_html", "st.html(", "components.html", "components.v1"):
        assert forbidden not in text, forbidden


# --- nothing secret or generated in the repository ---------------------------------------


def test_no_secrets_in_tracked_or_new_files() -> None:
    for path in committable_files():
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not secret_findings(relative, text), relative


def test_the_secret_scan_flags_keys_and_allows_fake_env_lines_in_tests() -> None:
    fake_env_line = "ANTHROPIC_API_KEY=" + "placeholder-not-a-key"
    key_shaped = "sk-ant-" + "x" * 20
    assert secret_findings("tests/test_x.py", fake_env_line) == []
    assert secret_findings("src/decision_memory/config.py", fake_env_line)
    assert secret_findings("docs/README.md", fake_env_line)
    assert secret_findings("tests/test_x.py", key_shaped)  # never allowed, even in tests
    assert secret_findings(".env.example", "ANTHROPIC_API_KEY=") == []


def test_env_files_databases_and_venv_are_not_tracked() -> None:
    names = [str(path.relative_to(REPO_ROOT)) for path in tracked_files()]
    assert ".env" not in names
    assert not [n for n in names if n.endswith((".db", ".db-journal", ".db-wal"))]
    assert not [n for n in names if n.startswith(".venv/")]
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in (".env", ".venv/", "data/*.db"):
        assert pattern in ignored


def test_env_example_holds_placeholders_only() -> None:
    values = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    assert values["ANTHROPIC_API_KEY"] == ""
    assert values["LLM_MODE"] == "offline"
