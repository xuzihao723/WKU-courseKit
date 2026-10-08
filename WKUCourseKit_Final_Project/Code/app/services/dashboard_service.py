from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Term
from app.services.course_search import search_courses
from app.services.material_service import MaterialRow, list_materials_for_enrolled_courses


def get_current_term(db: Session) -> Term | None:
    return db.scalar(select(Term).order_by(Term.code.desc()).limit(1))


def current_term_summary(db: Session, *, student_key: str) -> dict[str, object]:
    current_term = get_current_term(db)
    term_code = current_term.code if current_term else None
    courses = search_courses(db, term=term_code, sort="-updated_date") if term_code else search_courses(db)
    material_rows = list_materials_for_enrolled_courses(db, student_key=student_key, term=term_code)
    return {
        "current_term": current_term,
        "courses": courses,
        "material_rows": material_rows,
        "status_counts": count_material_statuses(material_rows),
    }


def courses_needing_syllabus_review(db: Session, *, student_key: str) -> list:
    summary = current_term_summary(db, student_key=student_key)
    return [
        course
        for course in summary["courses"]
        if course.syllabus and course.syllabus.status == "not_reviewed"
    ]


def recently_updated_syllabi(db: Session, *, student_key: str, limit: int = 5) -> list:
    summary = current_term_summary(db, student_key=student_key)
    return sorted(
        [course for course in summary["courses"] if course.syllabus],
        key=lambda course: course.syllabus.updated_at,
        reverse=True,
    )[:limit]


def materials_still_needed(db: Session, *, student_key: str) -> list[MaterialRow]:
    summary = current_term_summary(db, student_key=student_key)
    return [
        row
        for row in summary["material_rows"]
        if row.status and row.status.status in {"needed", "ordered"}
    ]


def dashboard_context(db: Session, *, student_key: str) -> dict[str, object]:
    summary = current_term_summary(db, student_key=student_key)
    review_needed = [
        course
        for course in summary["courses"]
        if course.syllabus and course.syllabus.status == "not_reviewed"
    ]
    recent_updates = sorted(
        [course for course in summary["courses"] if course.syllabus],
        key=lambda course: course.syllabus.updated_at,
        reverse=True,
    )[:5]
    material_needed = [
        row
        for row in summary["material_rows"]
        if row.status and row.status.status in {"needed", "ordered"}
    ]
    return {
        **summary,
        "review_needed": review_needed,
        "recent_updates": recent_updates,
        "material_needed": material_needed,
    }


def count_material_statuses(rows: list[MaterialRow]) -> dict[str, int]:
    counts = {"needed": 0, "ordered": 0, "owned": 0, "borrowed": 0}
    for row in rows:
        if row.status and row.status.status in counts:
            counts[row.status.status] += 1
    return counts

