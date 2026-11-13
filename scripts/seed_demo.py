#!/usr/bin/env python3
"""Load the synthetic fixtures into the demo database. Safe to run repeatedly.

    python scripts/seed_demo.py            # uses DM_DB_PATH or data/demo.db
    python scripts/seed_demo.py --db PATH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from decision_memory.config import SettingsError, load_settings  # noqa: E402
from decision_memory.seed import FixtureError, format_counts, seed_database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, help="SQLite file (default: DM_DB_PATH or data/demo.db)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        db_path = args.db or settings.db_path
        counts = seed_database(db_path, settings.fixtures_dir)
    except (SettingsError, FixtureError) as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    print(f"Seeded synthetic demo data into {db_path}")
    print(format_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
