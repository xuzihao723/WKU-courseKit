from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class InstructorPayload(FlexibleModel):
    full_name: str
    email: str | None = None
    department: str | None = None
    office: str | None = None


class SyllabusSectionPayload(FlexibleModel):
    heading: str
    body: str
    sort_order: int = 0


class SyllabusPayload(FlexibleModel):
    source_label: str = "Kean Simple Syllabus"
    status: str = "not_reviewed"
    updated_at: str | None = None
    summary: str = ""
    sections: list[SyllabusSectionPayload] = Field(default_factory=list)


class MaterialPayload(FlexibleModel):
    title: str
    authors: str = ""
    material_type: str = "textbook"
    isbn_10: str | None = None
    isbn_13: str | None = None
    edition: str | None = None
    publisher: str | None = None
    requirement_status: str = "required"
    student_status: str = "needed"
    student_note: str = ""
    note: str = ""
    legal_search_url: str | None = None
    library_search_url: str | None = None
    bookstore_search_url: str | None = None


class CoursePayload(FlexibleModel):
    subject: str
    course_number: str
    section: str = ""
    title: str
    campus: str = "Wenzhou-Kean University"
    instructor: InstructorPayload
    syllabus: SyllabusPayload
    materials: list[MaterialPayload] = Field(default_factory=list)


class TermPayload(FlexibleModel):
    code: str
    name: str
    starts_on: str | None = None
    ends_on: str | None = None
    courses: list[CoursePayload] = Field(default_factory=list)


class SimpleSyllabusExportPayload(FlexibleModel):
    student_key: str = "kean-student"
    terms: list[TermPayload]
