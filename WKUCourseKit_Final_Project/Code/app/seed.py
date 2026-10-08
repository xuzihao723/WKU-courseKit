from __future__ import annotations

import argparse
from pathlib import Path

from app.database import SessionLocal, init_db
from app.services.importer import DEFAULT_MOCK_DATA_PATH, load_mock_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed WKUCourseKit mock syllabus data.")
    parser.add_argument("--path", type=Path, default=DEFAULT_MOCK_DATA_PATH)
    parser.add_argument("--reset", action="store_true", help="Clear existing SQLite data before import.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    with SessionLocal() as db:
        counts = load_mock_data(db, args.path, reset=args.reset)
    print("Seeded WKUCourseKit data:")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()

