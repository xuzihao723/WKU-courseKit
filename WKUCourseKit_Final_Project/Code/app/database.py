from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "wkcoursekit.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns()


def ensure_sqlite_columns() -> None:
    inspector = inspect(engine)
    if "courses" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("courses")}
    additions = {
        "simple_syllabus_doc_code": "VARCHAR(80)",
        "simple_syllabus_url": "VARCHAR(500)",
        "term_external_id": "VARCHAR(80)",
        "entity_external_id": "VARCHAR(80)",
        "material_count_hint": "INTEGER DEFAULT 0 NOT NULL",
    }
    with engine.begin() as connection:
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE courses ADD COLUMN {column} {definition}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
