from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Term(Base):
    __tablename__ = "terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    courses: Mapped[list["Course"]] = relationship(back_populates="term")


class Instructor(Base):
    __tablename__ = "instructors"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    office: Mapped[str | None] = mapped_column(String(120), nullable=True)

    courses: Mapped[list["Course"]] = relationship(back_populates="instructor")


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(16), index=True)
    course_number: Mapped[str] = mapped_column(String(16), index=True)
    section: Mapped[str] = mapped_column(String(16), default="")
    title: Mapped[str] = mapped_column(String(180), index=True)
    campus: Mapped[str] = mapped_column(String(80), default="Wenzhou-Kean University")
    simple_syllabus_doc_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    simple_syllabus_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    term_external_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_external_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    material_count_hint: Mapped[int] = mapped_column(Integer, default=0)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    instructor_id: Mapped[int] = mapped_column(ForeignKey("instructors.id"))

    term: Mapped["Term"] = relationship(back_populates="courses")
    instructor: Mapped["Instructor"] = relationship(back_populates="courses")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="course")
    syllabus: Mapped["Syllabus | None"] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    materials: Mapped[list["CourseMaterial"]] = relationship(back_populates="course", cascade="all, delete-orphan")

    @property
    def code(self) -> str:
        section = f"-{self.section}" if self.section else ""
        return f"{self.subject} {self.course_number}{section}"


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_key", "course_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[str] = mapped_column(String(80), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    course: Mapped["Course"] = relationship(back_populates="enrollments")


class Syllabus(Base):
    __tablename__ = "syllabi"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), unique=True)
    source_label: Mapped[str] = mapped_column(String(120), default="Mock Simple Syllabus")
    status: Mapped[str] = mapped_column(String(32), default="not_reviewed")
    summary: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    course: Mapped["Course"] = relationship(back_populates="syllabus")
    sections: Mapped[list["SyllabusSection"]] = relationship(
        back_populates="syllabus",
        cascade="all, delete-orphan",
        order_by="SyllabusSection.sort_order",
    )


class SyllabusSection(Base):
    __tablename__ = "syllabus_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    syllabus_id: Mapped[int] = mapped_column(ForeignKey("syllabi.id"))
    heading: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    syllabus: Mapped["Syllabus"] = relationship(back_populates="sections")


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240), index=True)
    authors: Mapped[str] = mapped_column(String(240), default="")
    material_type: Mapped[str] = mapped_column(String(40), default="textbook")
    isbn_key: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    isbn_10: Mapped[str | None] = mapped_column(String(16), nullable=True)
    isbn_13: Mapped[str | None] = mapped_column(String(20), nullable=True)
    edition: Mapped[str | None] = mapped_column(String(80), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(120), nullable=True)
    legal_search_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    library_search_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bookstore_search_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    courses: Mapped[list["CourseMaterial"]] = relationship(back_populates="material")
    student_statuses: Mapped[list["StudentMaterialStatus"]] = relationship(back_populates="material")


class CourseMaterial(Base):
    __tablename__ = "course_materials"
    __table_args__ = (UniqueConstraint("course_id", "material_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    requirement_status: Mapped[str] = mapped_column(String(32), default="required")
    note: Mapped[str] = mapped_column(Text, default="")

    course: Mapped["Course"] = relationship(back_populates="materials")
    material: Mapped["Material"] = relationship(back_populates="courses")


class StudentMaterialStatus(Base):
    __tablename__ = "student_material_statuses"
    __table_args__ = (UniqueConstraint("student_key", "material_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[str] = mapped_column(String(80), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    status: Mapped[str] = mapped_column(String(32), default="needed")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    material: Mapped["Material"] = relationship(back_populates="student_statuses")
