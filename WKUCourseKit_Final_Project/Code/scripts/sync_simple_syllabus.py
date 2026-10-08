from __future__ import annotations

import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.database import SessionLocal, init_db
from app.services.simple_syllabus import SimpleSyllabusImportError
from app.services.simple_syllabus_scraper import sync_from_logged_in_browser, write_simple_syllabus_auth_status


def main() -> int:
    init_db()
    try:
        with SessionLocal() as db:
            sync_from_logged_in_browser(db, reset=True)
    except SimpleSyllabusImportError as exc:
        write_simple_syllabus_auth_status("error", str(exc))
        return 1
    except Exception as exc:
        write_simple_syllabus_auth_status(
            "error",
            f"Could not refresh Kean Simple Syllabus login. {type(exc).__name__}: {exc}",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
