from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Course, CourseMaterial, Material, StudentMaterialStatus, Term, utc_now


VALID_MATERIAL_STATUSES = {"needed", "owned", "borrowed", "ordered"}
ZLIB_BASE_URL = "https://z-lib.by"


@dataclass(frozen=True)
class MaterialRow:
    material: Material
    course_material: CourseMaterial
    status: StudentMaterialStatus | None
    course: Course


def list_materials_for_enrolled_courses(
    db: Session,
    *,
    student_key: str,
    q: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    requirement: str | None = None,
) -> list[MaterialRow]:
    statement = (
        select(CourseMaterial, StudentMaterialStatus)
        .join(CourseMaterial.course)
        .join(Course.term)
        .join(CourseMaterial.material)
        .join(Course.enrollments)
        .outerjoin(
            StudentMaterialStatus,
            (StudentMaterialStatus.material_id == Material.id)
            & (StudentMaterialStatus.student_key == student_key),
        )
        .options(
            selectinload(CourseMaterial.course).selectinload(Course.term),
            selectinload(CourseMaterial.course).selectinload(Course.instructor),
            selectinload(CourseMaterial.material),
        )
        .where(Course.enrollments.any(student_key=student_key, is_active=True))
    )

    if q:
        needle = q.lower().strip()
        compact = needle.replace(" ", "")
        statement = statement.where(
            func.lower(Material.title).contains(needle)
            | func.lower(Material.authors).contains(needle)
            | func.lower(Material.isbn_key).contains(compact)
            | func.lower(Course.subject + Course.course_number).contains(compact)
        )
    if term:
        statement = statement.where(Term.code == term.strip().upper())
    if subject:
        statement = statement.where(Course.subject == subject.strip().upper())
    if requirement:
        statement = statement.where(CourseMaterial.requirement_status == requirement)
    if status:
        statement = statement.where(StudentMaterialStatus.status == status)

    rows = db.execute(statement.order_by(Course.subject.asc(), Course.course_number.asc(), Material.title.asc())).all()
    return [
        MaterialRow(
            material=course_material.material,
            course_material=course_material,
            status=student_status,
            course=course_material.course,
        )
        for course_material, student_status in rows
    ]


def group_materials_by_course(rows: list[MaterialRow]) -> dict[int, list[MaterialRow]]:
    grouped: dict[int, list[MaterialRow]] = {}
    for row in rows:
        grouped.setdefault(row.course.id, []).append(row)
    return grouped


def deduplicate_materials_by_isbn(rows: list[MaterialRow]) -> list[Material]:
    seen: set[str] = set()
    deduped: list[Material] = []
    for row in rows:
        key = row.material.isbn_key or f"title:{row.material.title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row.material)
    return deduped


def legal_source_links(material: Material) -> dict[str, str]:
    isbn = material.isbn_13 or material.isbn_10
    query = material_source_query(material)
    encoded = quote_plus(query)
    worldcat_url = material.legal_search_url if isbn and material.legal_search_url else f"https://www.worldcat.org/search?q={encoded}"
    return {
        "worldcat": worldcat_url,
        "library": material.library_search_url
        or f"https://kean-primo.hosted.exlibrisgroup.com/primo-explore/search?query=any,contains,{encoded}",
        "bookstore": material.bookstore_search_url or "https://bncvirtual.com/kean",
        "zlib": zlib_search_url(query),
    }


def material_source_query(material: Material) -> str:
    return (material.isbn_13 or material.isbn_10 or " ".join(part for part in (material.title, material.authors) if part)).strip()


def zlib_search_url(query: str) -> str:
    normalized = " ".join(query.split())
    if not normalized:
        return f"{ZLIB_BASE_URL}/"
    return f"{ZLIB_BASE_URL}/s/{quote(normalized, safe='')}"


def update_student_material_status(
    db: Session,
    *,
    student_key: str,
    material_id: int,
    status: str,
) -> StudentMaterialStatus:
    if status not in VALID_MATERIAL_STATUSES:
        raise ValueError(f"Unsupported material status: {status}")

    existing = db.scalar(
        select(StudentMaterialStatus).where(
            StudentMaterialStatus.student_key == student_key,
            StudentMaterialStatus.material_id == material_id,
        )
    )
    if existing is None:
        existing = StudentMaterialStatus(student_key=student_key, material_id=material_id, status=status)
        db.add(existing)
    else:
        existing.status = status
        existing.updated_at = utc_now()
    db.commit()
    db.refresh(existing)
    return existing


def backfill_legal_links(material: Material) -> None:
    links = legal_source_links(material)
    material.legal_search_url = material.legal_search_url or links["worldcat"]
    material.library_search_url = material.library_search_url or links["library"]
    material.bookstore_search_url = material.bookstore_search_url or links["bookstore"]
