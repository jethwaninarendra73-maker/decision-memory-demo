"""Demo databases for a hosted copy of the app.

On a laptop the app uses one database (`data/demo.db`). On a shared host every visitor
would then see everyone else's captures, approvals and resets, so with
`DM_PER_SESSION_DB` on, each browser session gets its own fresh copy of the synthetic
baseline in a temporary folder. Copies older than a few hours are deleted.

Nothing here is specific to Streamlit: the caller passes the session's ID and the time.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .seed import seed_database

logger = logging.getLogger(__name__)

MAX_AGE_SECONDS = 6 * 60 * 60  # a visitor's copy is kept for six hours after last use
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


def ensure_seeded(db_path: Path, fixtures_dir: Path) -> bool:
    """Create the synthetic baseline if the database file is missing or empty. True if it
    was created now. An existing database is left exactly as it is."""
    if db_path.exists() and db_path.stat().st_size > 0:
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    seed_database(db_path, fixtures_dir)
    logger.info("Created the synthetic demo baseline")
    return True


def prune(session_dir: Path, now: float, max_age_seconds: float = MAX_AGE_SECONDS) -> int:
    """Delete visitor databases not touched for `max_age_seconds`. Returns how many."""
    if not session_dir.is_dir():
        return 0
    removed = 0
    for path in session_dir.iterdir():
        if not path.is_file() or not SESSION_ID.match(path.name.split(".", 1)[0]):
            continue  # only this module's own files
        if now - path.stat().st_mtime > max_age_seconds:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def session_db(
    session_dir: Path, session_id: str, fixtures_dir: Path, now: float | None = None
) -> Path:
    """This session's own database, created from the baseline on first use."""
    if not SESSION_ID.match(session_id):
        raise ValueError("A session ID is 32 lowercase hex characters.")
    session_dir.mkdir(parents=True, exist_ok=True)
    prune(session_dir, time.time() if now is None else now)
    path = session_dir / f"{session_id}.db"
    if not ensure_seeded(path, fixtures_dir):
        path.touch()  # in use: keep it from being pruned
    return path
