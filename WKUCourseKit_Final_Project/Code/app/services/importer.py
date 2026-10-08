from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Course,
    CourseMaterial,
    Enrollment,
    Instructor,
    Material,
    StudentMaterialStatus,
    Syllabus,
    SyllabusSection,
    Term,
    utc_now,
)


DEFAULT_MOCK_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_syllabus.json"


def load_mock_data(db: Session, json_path: Path | str = DEFAULT_MOCK_DATA_PATH, reset: bool = False) -> dict[str, int]:
    path = Path(json_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return load_syllabus_payload(db, payload, reset=reset)


def load_syllabus_payload(db: Session, payload: dict[str, Any], reset: bool = False) -> dict[str, int]:
    if reset:
        clear_database(db)
        clear_simple_syllabus_page_cache()

    student_key = payload.get("student_key", "demo-student")
    counts = {
        "terms": 0,
        "instructors": 0,
        "courses": 0,
        "syllabi": 0,
        "sections": 0,
        "materials": 0,
        "course_materials": 0,
        "student_material_statuses": 0,
    }

    for term_data in payload.get("terms", []):
        term, created = get_or_create_term(db, term_data)
        counts["terms"] += int(created)

        for course_data in term_data.get("courses", []):
            instructor, created = get_or_create_instructor(db, course_data["instructor"])
            counts["instructors"] += int(created)

            course, created = get_or_create_course(db, term, instructor, course_data)
            counts["courses"] += int(created)
            enroll_course = bool(course_data.get("enrolled", payload.get("enroll_courses", True)))
            if enroll_course:
                ensure_enrollment(db, student_key, course)

            syllabus_counts = replace_syllabus(db, course, course_data["syllabus"])
            counts["syllabi"] += syllabus_counts["syllabi"]
            counts["sections"] += syllabus_counts["sections"]

            material_counts = replace_course_materials(
                db,
                student_key,
                course,
                course_data.get("materials", []),
                create_student_status=enroll_course,
            )
            for key, value in material_counts.items():
                counts[key] += value

    db.commit()
    return counts


def clear_database(db: Session) -> None:
    for model in (
        StudentMaterialStatus,
        CourseMaterial,
        SyllabusSection,
        Syllabus,
        Enrollment,
        Course,
        Material,
        Instructor,
        Term,
    ):
        db.execute(delete(model))


def clear_simple_syllabus_page_cache() -> None:
    try:
        from app.database import BASE_DIR
    except Exception:
        return
    for filename in (".simple_syllabus_library_meta.json", ".simple_syllabus_library_pages.json"):
        try:
            (BASE_DIR / filename).unlink(missing_ok=True)
        except OSError:
            continue


def get_or_create_term(db: Session, term_data: dict[str, Any]) -> tuple[Term, bool]:
    term = db.scalar(select(Term).where(Term.code == term_data["code"]))
    created = term is None
    if term is None:
        term = Term(code=term_data["code"], name=term_data["name"])
        db.add(term)
    term.name = term_data["name"]
    term.starts_on = parse_date(term_data.get("starts_on"))
    term.ends_on = parse_date(term_data.get("ends_on"))
    db.flush()
    return term, created


def get_or_create_instructor(db: Session, instructor_data: dict[str, Any]) -> tuple[Instructor, bool]:
    instructor = db.scalar(select(Instructor).where(Instructor.full_name == instructor_data["full_name"]))
    created = instructor is None
    if instructor is None:
        instructor = Instructor(full_name=instructor_data["full_name"])
        db.add(instructor)
    instructor.email = instructor_data.get("email")
    instructor.department = instructor_data.get("department")
    instructor.office = instructor_data.get("office")
    db.flush()
    return instructor, created


def get_or_create_course(
    db: Session,
    term: Term,
    instructor: Instructor,
    course_data: dict[str, Any],
) -> tuple[Course, bool]:
    course = db.scalar(
        select(Course).where(
            Course.term_id == term.id,
            Course.subject == course_data["subject"].upper(),
            Course.course_number == course_data["course_number"],
            Course.section == course_data.get("section", ""),
        )
    )
    created = course is None
    if course is None:
        course = Course(
            term=term,
            instructor=instructor,
            subject=course_data["subject"].upper(),
            course_number=course_data["course_number"],
            section=course_data.get("section", ""),
            title=course_data["title"],
        )
        db.add(course)

    course.instructor = instructor
    course.title = course_data["title"]
    course.campus = course_data.get("campus", "Wenzhou-Kean University")
    course.simple_syllabus_doc_code = course_data.get("simple_syllabus_doc_code") or course.simple_syllabus_doc_code
    course.simple_syllabus_url = course_data.get("simple_syllabus_url") or course.simple_syllabus_url
    course.term_external_id = course_data.get("term_external_id") or course.term_external_id
    course.entity_external_id = course_data.get("entity_external_id") or course.entity_external_id
    course.material_count_hint = int(course_data.get("material_count_hint") or course.material_count_hint or 0)
    db.flush()
    return course, created


def ensure_enrollment(db: Session, student_key: str, course: Course) -> None:
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.student_key == student_key,
            Enrollment.course_id == course.id,
        )
    )
    if enrollment is None:
        db.add(Enrollment(student_key=student_key, course=course))


def replace_syllabus(db: Session, course: Course, syllabus_data: dict[str, Any]) -> dict[str, int]:
    if course.syllabus is None:
        syllabus = Syllabus(course=course)
        db.add(syllabus)
        created = 1
    else:
        syllabus = course.syllabus
        created = 0

    syllabus.source_label = syllabus_data.get("source_label", "Mock Simple Syllabus")
    syllabus.status = syllabus_data.get("status", "not_reviewed")
    syllabus.summary = syllabus_data.get("summary", "")
    syllabus.updated_at = parse_datetime(syllabus_data.get("updated_at")) or utc_now()
    db.flush()

    db.execute(delete(SyllabusSection).where(SyllabusSection.syllabus_id == syllabus.id))
    section_count = 0
    for section_data in syllabus_data.get("sections", []):
        db.add(
            SyllabusSection(
                syllabus=syllabus,
                heading=section_data["heading"],
                body=section_data["body"],
                sort_order=section_data.get("sort_order", section_count * 10),
            )
        )
        section_count += 1
    db.flush()
    return {"syllabi": created, "sections": section_count}


def replace_course_materials(
    db: Session,
    student_key: str,
    course: Course,
    materials_data: list[dict[str, Any]],
    *,
    create_student_status: bool = True,
) -> dict[str, int]:
    db.execute(delete(CourseMaterial).where(CourseMaterial.course_id == course.id))
    counts = {"materials": 0, "course_materials": 0, "student_material_statuses": 0}

    for material_data in materials_data:
        material, created = get_or_create_material(db, material_data)
        counts["materials"] += int(created)

        db.add(
            CourseMaterial(
                course=course,
                material=material,
                requirement_status=material_data.get("requirement_status", "required"),
                note=material_data.get("note", ""),
            )
        )
        counts["course_materials"] += 1

        if create_student_status:
            status, created = get_or_create_student_material_status(db, student_key, material)
            status.status = material_data.get("student_status", status.status)
            status.note = material_data.get("student_note", "")
            counts["student_material_statuses"] += int(created)

    db.flush()
    return counts


def get_or_create_material(db: Session, material_data: dict[str, Any]) -> tuple[Material, bool]:
    isbn_key = normalize_isbn(material_data.get("isbn_13") or material_data.get("isbn_10"))
    material = None
    if isbn_key:
        material = db.scalar(select(Material).where(Material.isbn_key == isbn_key))

    created = material is None
    if material is None:
        material = Material(title=material_data["title"], isbn_key=isbn_key)
        db.add(material)

    material.title = material_data["title"]
    material.authors = material_data.get("authors", "")
    material.material_type = material_data.get("material_type", "textbook")
    material.isbn_10 = normalize_isbn(material_data.get("isbn_10"))
    material.isbn_13 = normalize_isbn(material_data.get("isbn_13"))
    material.edition = material_data.get("edition")
    material.publisher = material_data.get("publisher")
    link_query = material.isbn_13 or material.isbn_10 or material.title
    material.legal_search_url = material_data.get("legal_search_url") or f"https://www.worldcat.org/search?q={link_query}"
    material.library_search_url = material_data.get("library_search_url") or (
        "https://kean-primo.hosted.exlibrisgroup.com/primo-explore/search"
        f"?query=any,contains,{link_query}"
    )
    material.bookstore_search_url = material_data.get("bookstore_search_url") or "https://bncvirtual.com/kean"
    db.flush()
    return material, created


def get_or_create_student_material_status(
    db: Session,
    student_key: str,
    material: Material,
) -> tuple[StudentMaterialStatus, bool]:
    status = db.scalar(
        select(StudentMaterialStatus).where(
            StudentMaterialStatus.student_key == student_key,
            StudentMaterialStatus.material_id == material.id,
        )
    )
    created = status is None
    if status is None:
        status = StudentMaterialStatus(student_key=student_key, material=material)
        db.add(status)
    return status, created


def normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", value).upper()
    return normalized or None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
