from __future__ import annotations

from typing import Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Course, CourseMaterial, Enrollment, Instructor, Syllabus, SyllabusSection, Term


CourseSort = Literal["course_code", "title", "updated_date", "-updated_date"]


def search_courses(
    db: Session,
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    syllabus_keyword: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
    sort: CourseSort = "course_code",
    student_key: str | None = None,
    enrolled_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[Course]:
    if limit is not None or offset:
        id_statement = apply_sort(
            build_course_id_query(
                q=q,
                course_code=course_code,
                title=title,
                instructor=instructor,
                syllabus_keyword=syllabus_keyword,
                term=term,
                subject=subject,
                has_materials=has_materials,
                student_key=student_key,
                enrolled_only=enrolled_only,
            ),
            sort,
        )
        if offset:
            id_statement = id_statement.offset(offset)
        if limit is not None:
            id_statement = id_statement.limit(limit)
        course_ids = list(db.scalars(id_statement))
        if not course_ids:
            return []
        position = {course_id: index for index, course_id in enumerate(course_ids)}
        courses = list(
            db.scalars(
                select(Course)
                .where(Course.id.in_(course_ids))
                .options(*course_load_options())
            ).unique()
        )
        return sorted(courses, key=lambda course: position[course.id])

    statement = base_course_query().outerjoin(Syllabus.sections)
    statement = apply_course_filters(
        statement,
        q=q,
        course_code=course_code,
        title=title,
        instructor=instructor,
        syllabus_keyword=syllabus_keyword,
        term=term,
        subject=subject,
        has_materials=has_materials,
        student_key=student_key,
        enrolled_only=enrolled_only,
    )

    return list(db.scalars(apply_sort(statement, sort)).unique())


def count_courses(
    db: Session,
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    syllabus_keyword: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
    student_key: str | None = None,
    enrolled_only: bool = False,
) -> int:
    id_statement = build_course_id_query(
        q=q,
        course_code=course_code,
        title=title,
        instructor=instructor,
        syllabus_keyword=syllabus_keyword,
        term=term,
        subject=subject,
        has_materials=has_materials,
        student_key=student_key,
        enrolled_only=enrolled_only,
    )
    return int(db.scalar(select(func.count()).select_from(id_statement.subquery())) or 0)


def build_course_id_query(
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    syllabus_keyword: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
    student_key: str | None = None,
    enrolled_only: bool = False,
) -> Select[tuple[int]]:
    statement = (
        select(Course.id)
        .join(Course.term)
        .join(Course.instructor)
        .outerjoin(Course.syllabus)
        .outerjoin(Syllabus.sections)
        .distinct()
    )
    return apply_course_filters(
        statement,
        q=q,
        course_code=course_code,
        title=title,
        instructor=instructor,
        syllabus_keyword=syllabus_keyword,
        term=term,
        subject=subject,
        has_materials=has_materials,
        student_key=student_key,
        enrolled_only=enrolled_only,
    )


def apply_course_filters(
    statement: Select,
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    syllabus_keyword: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: bool | None = None,
    student_key: str | None = None,
    enrolled_only: bool = False,
) -> Select:
    if student_key or enrolled_only:
        statement = statement.join(Course.enrollments).where(Enrollment.is_active.is_(True))
        if student_key:
            statement = statement.where(Enrollment.student_key == student_key)
    if q:
        statement = statement.where(build_general_search_clause(q))
    if course_code:
        compact = normalize_course_code_query(course_code)
        statement = statement.where(func.lower(Course.subject + Course.course_number + Course.section).contains(compact))
    if title:
        statement = statement.where(func.lower(Course.title).contains(title.lower().strip()))
    if instructor:
        statement = statement.where(func.lower(Instructor.full_name).contains(instructor.lower().strip()))
    if syllabus_keyword:
        keyword = syllabus_keyword.lower().strip()
        statement = statement.where(
            or_(
                func.lower(Syllabus.summary).contains(keyword),
                func.lower(SyllabusSection.heading).contains(keyword),
                func.lower(SyllabusSection.body).contains(keyword),
            )
        )
    if term:
        statement = statement.where(Term.code == term.strip().upper())
    if subject:
        statement = statement.where(Course.subject == subject.strip().upper())
    if has_materials is True:
        statement = statement.where(Course.materials.any())
    elif has_materials is False:
        statement = statement.where(~Course.materials.any())
    return statement


def search_by_course_code(db: Session, query: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, course_code=query, sort=sort)


def search_by_title(db: Session, query: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, title=query, sort=sort)


def search_by_instructor(db: Session, query: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, instructor=query, sort=sort)


def search_by_syllabus_keyword(db: Session, query: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, syllabus_keyword=query, sort=sort)


def filter_by_term(db: Session, term: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, term=term, sort=sort)


def filter_by_subject(db: Session, subject: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, subject=subject, sort=sort)


def filter_by_instructor(db: Session, instructor: str, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, instructor=instructor, sort=sort)


def filter_by_has_materials(db: Session, has_materials: bool = True, *, sort: CourseSort = "course_code") -> list[Course]:
    return search_courses(db, has_materials=has_materials, sort=sort)


def base_course_query() -> Select[tuple[Course]]:
    return (
        select(Course)
        .join(Course.term)
        .join(Course.instructor)
        .outerjoin(Course.syllabus)
        .options(*course_load_options())
    )


def course_load_options():
    return (
        selectinload(Course.term),
        selectinload(Course.instructor),
        selectinload(Course.syllabus).selectinload(Syllabus.sections),
        selectinload(Course.materials).selectinload(CourseMaterial.material),
    )


def build_general_search_clause(query: str):
    compact = normalize_course_code_query(query)
    needle = query.lower().strip()
    return or_(
        func.lower(Course.subject + Course.course_number + Course.section).contains(compact),
        func.lower(Course.title).contains(needle),
        func.lower(Instructor.full_name).contains(needle),
        func.lower(Syllabus.summary).contains(needle),
        func.lower(SyllabusSection.heading).contains(needle),
        func.lower(SyllabusSection.body).contains(needle),
    )


def apply_sort(statement: Select[tuple[Course]], sort: CourseSort) -> Select[tuple[Course]]:
    if sort == "title":
        return statement.order_by(Course.title.asc(), Course.subject.asc(), Course.course_number.asc(), Course.section.asc())
    if sort == "updated_date":
        return statement.order_by(Syllabus.updated_at.asc(), Course.subject.asc(), Course.course_number.asc(), Course.section.asc())
    if sort == "-updated_date":
        return statement.order_by(Syllabus.updated_at.desc(), Course.subject.asc(), Course.course_number.asc(), Course.section.asc())
    return statement.order_by(Course.subject.asc(), Course.course_number.asc(), Course.section.asc())


def normalize_course_code_query(value: str) -> str:
    return "".join(character for character in value.lower().strip() if character.isalnum())
