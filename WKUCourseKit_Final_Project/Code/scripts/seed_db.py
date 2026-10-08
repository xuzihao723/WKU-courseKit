from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal, init_db
from app.services.importer import DEFAULT_MOCK_DATA_PATH, load_mock_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed WKCourseKit SQLite demo data.")
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_MOCK_DATA_PATH,
        help="Path to mock Simple Syllabus-style JSON data.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    with SessionLocal() as db:
        counts = load_mock_data(db, args.path, reset=True)

    print("Seeded WKCourseKit SQLite database from mock syllabus data.")
    print(f"Source: {args.path}")
    for key, value in counts.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

