from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Course, CourseMaterial, Enrollment, Instructor, Syllabus, Term
from app.services.course_search import CourseSort, count_courses, search_courses
from app.services.dashboard_service import dashboard_context, get_current_term
from app.services.importer import DEFAULT_MOCK_DATA_PATH, load_mock_data
from app.services.material_service import MaterialRow, list_materials_for_enrolled_courses


DEMO_STUDENT_KEY = "demo-student"


@dataclass(frozen=True)
class CourseSectionGroup:
    primary: Course
    sections: list[Course]

    @property
    def count(self) -> int:
        return 1 + len(self.sections)


def ensure_demo_data(db: Session, json_path: Path | str = DEFAULT_MOCK_DATA_PATH) -> None:
    course_count = db.scalar(select(func.count()).select_from(Course))
    if course_count == 0:
        load_mock_data(db, json_path)


def get_terms(db: Session) -> list[Term]:
    return list(db.scalars(select(Term).order_by(Term.code.desc())))


def get_latest_term_code(db: Session) -> str | None:
    terms = list(db.scalars(select(Term)))
    if not terms:
        return None
    return max(terms, key=lambda term: term_sort_key(term)).code


def get_latest_enrolled_term_code(db: Session, student_key: str = DEMO_STUDENT_KEY) -> str | None:
    terms = list(
        db.scalars(
            select(Term)
            .join(Term.courses)
            .join(Course.enrollments)
            .where(Enrollment.student_key == student_key, Enrollment.is_active.is_(True))
            .distinct()
        )
    )
    if not terms:
        return None
    return max(terms, key=lambda term: term_sort_key(term)).code


def term_sort_key(term: Term) -> tuple[int, int, str]:
    if term.starts_on:
        return term.starts_on.year, term.starts_on.toordinal(), term.code
    text = term.code.upper().replace("/", "")
    year_match = re.search(r"(20)?(\d{2})", text)
    year = int(year_match.group(0)) if year_match and len(year_match.group(0)) == 4 else 2000 + int(year_match.group(2)) if year_match else 0
    if "FA" in text:
        season = 40
    elif "SU" in text or "S1" in text or "S2" in text:
        season = 30
    elif "SP" in text:
        season = 20
    elif "WI" in text or "WB" in text:
        season = 10
    else:
        season = 0
    return year, season, text


def get_subjects(db: Session) -> list[str]:
    return list(db.scalars(select(Course.subject).distinct().order_by(Course.subject.asc())))


def get_instructors(db: Session) -> list[Instructor]:
    return list(db.scalars(select(Instructor).order_by(Instructor.full_name.asc())))


def get_course(db: Session, course_id: int) -> Course | None:
    return db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.term),
            selectinload(Course.instructor),
            selectinload(Course.syllabus).selectinload(Syllabus.sections),
            selectinload(Course.materials).selectinload(CourseMaterial.material),
        )
    )


def get_today_context(db: Session, student_key: str = DEMO_STUDENT_KEY) -> dict[str, object]:
    return dashboard_context(db, student_key=student_key)


def get_course_results(
    db: Session,
    *,
    q: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    instructor: str | None = None,
    has_materials: bool | None = None,
    sort: CourseSort = "course_code",
) -> list[Course]:
    return search_courses(
        db,
        q=q,
        instructor=instructor,
        term=term,
        subject=subject,
        has_materials=has_materials,
        sort=sort,
        enrolled_only=True,
    )


def get_library_results(
    db: Session,
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
    sort: CourseSort = "-updated_date",
    limit: int | None = None,
    offset: int = 0,
) -> list[Course]:
    return search_courses(
        db,
        q=q,
        course_code=course_code,
        title=title,
        instructor=instructor,
        term=term,
        subject=subject,
        has_materials=has_materials,
        sort=sort,
        limit=limit,
        offset=offset,
    )


def get_courses_by_identity_keys(db: Session, keys: list[dict[str, str]]) -> list[Course]:
    if not keys:
        return []
    loaded: dict[tuple[str, str, str, str], Course] = {}
    for key in keys:
        term_code = str(key.get("term", ""))
        subject = str(key.get("subject", ""))
        course_number = str(key.get("course_number", ""))
        section = str(key.get("section", ""))
        if not term_code or not subject or not course_number:
            continue
        course = db.scalar(
            select(Course)
            .join(Course.term)
            .where(
                Term.code == term_code,
                Course.subject == subject,
                Course.course_number == course_number,
                Course.section == section,
            )
            .options(
                selectinload(Course.term),
                selectinload(Course.instructor),
                selectinload(Course.syllabus).selectinload(Syllabus.sections),
                selectinload(Course.materials).selectinload(CourseMaterial.material),
            )
        )
        if course:
            loaded[(term_code, subject, course_number, section)] = course
    ordered: list[Course] = []
    seen: set[int] = set()
    for key in keys:
        identity = (
            str(key.get("term", "")),
            str(key.get("subject", "")),
            str(key.get("course_number", "")),
            str(key.get("section", "")),
        )
        course = loaded.get(identity)
        if course and course.id not in seen:
            ordered.append(course)
            seen.add(course.id)
    return ordered


def group_course_sections(courses: list[Course]) -> list[CourseSectionGroup]:
    groups: dict[tuple[str, str, str], CourseSectionGroup] = {}
    ordered_keys: list[tuple[str, str, str]] = []
    for course in courses:
        key = (course.term.code if course.term else "", course.subject, course.course_number)
        group = groups.get(key)
        if group is None:
            groups[key] = CourseSectionGroup(primary=course, sections=[])
            ordered_keys.append(key)
        else:
            group.sections.append(course)
    return [groups[key] for key in ordered_keys]


def count_library_results(
    db: Session,
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
) -> int:
    return count_courses(
        db,
        q=q,
        course_code=course_code,
        title=title,
        instructor=instructor,
        term=term,
        subject=subject,
        has_materials=has_materials,
    )


def get_material_rows(
    db: Session,
    *,
    student_key: str = DEMO_STUDENT_KEY,
    q: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    requirement: str | None = None,
) -> list[MaterialRow]:
    return list_materials_for_enrolled_courses(
        db,
        student_key=student_key,
        q=q,
        term=term,
        subject=subject,
        status=status,
        requirement=requirement,
    )


def count_material_statuses(rows: list[MaterialRow]) -> dict[str, int]:
    counts = {"needed": 0, "ordered": 0, "owned": 0, "borrowed": 0}
    for row in rows:
        if row.status and row.status.status in counts:
            counts[row.status.status] += 1
    return counts


def parse_has_materials(value: str | None) -> bool | None:
    if value in {"yes", "true", "1"}:
        return True
    if value in {"no", "false", "0"}:
        return False
    return None
