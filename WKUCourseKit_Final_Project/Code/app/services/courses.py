"""Backward-compatible imports for the course search service."""

from app.services.course_search import (
    CourseSort,
    apply_sort,
    base_course_query,
    filter_by_has_materials,
    filter_by_instructor,
    filter_by_subject,
    filter_by_term,
    normalize_course_code_query,
    search_by_course_code,
    search_by_instructor,
    search_by_syllabus_keyword,
    search_by_title,
    search_courses,
)

