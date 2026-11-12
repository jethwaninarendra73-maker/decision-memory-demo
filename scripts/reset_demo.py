#!/usr/bin/env python3
"""Delete the demo database and rebuild it from the synthetic fixtures.

Everything created in the app (captured decisions, approvals, KPI updates) is removed.
The script refuses to delete a file that this app did not create.

    python scripts/reset_demo.py            # uses DM_DB_PATH or data/demo.db
    python scripts/reset_demo.py --db PATH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from decision_memory.config import SettingsError, load_settings  # noqa: E402
from decision_memory.seed import (  # noqa: E402
    FixtureError,
    ResetRefusedError,
    format_counts,
    reset_database,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, help="SQLite file (default: DM_DB_PATH or data/demo.db)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        db_path = args.db or settings.db_path
        counts = reset_database(db_path, settings.fixtures_dir)
    except (SettingsError, FixtureError, ResetRefusedError) as exc:
        print(f"Reset failed: {exc}", file=sys.stderr)
        return 1
    print(f"Reset {db_path} to the synthetic baseline")
    print(format_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
