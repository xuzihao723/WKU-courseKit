from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "https://kean-ss.colleague.elluciancloud.com"
COURSES_URL = f"{BASE_URL}/Student/Courses"
OPTIONS_URL = f"{BASE_URL}/Student/Courses/GetCatalogAdvancedSearch"
SEARCH_URL = f"{BASE_URL}/Student/Courses/PostSearchCriteria"
OFFICIAL_FALL_2026_WENZHOU_URL = (
    f"{BASE_URL}/Student/Student/Courses/Search?searchResultsView=SectionListing&terms=2026FAW"
)
DEFAULT_TERM = "2026FAW"
DEFAULT_TERM_NAME = "Fall 2026 Wenzhou"
DEFAULT_LOCATION = "W"
DEFAULT_LOCATION_NAME = "WENZHOU-KEAN UNIVERSITY"
CATALOG_PAGE_SIZE = 30
CACHE_DIR = Path("data") / "cache" / "course_catalog"
TOKEN_RE = re.compile(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"')


class CourseCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogOption:
    value: str
    label: str
    count: int | None = None


@dataclass(frozen=True)
class CatalogSection:
    id: str
    course_id: str
    subject: str
    course_number: str
    section: str
    title: str
    term: str
    location: str
    dates: str
    credits: str
    instructors: str
    meetings: tuple[str, ...]
    availability_status: str
    availability: str
    available: int | None
    capacity: int | None
    enrolled: int | None
    waitlisted: int | None
    methods: tuple[str, ...]
    comments: str
    bookstore_url: str
    description: str

    @property
    def code(self) -> str:
        return f"{self.subject} {self.course_number}-{self.section}".strip("-")


@dataclass(frozen=True)
class CatalogSearchResult:
    sections: tuple[CatalogSection, ...]
    total: int
    total_pages: int
    page: int
    page_size: int
    subjects: tuple[CatalogOption, ...]
    faculty: tuple[CatalogOption, ...]
    locations: tuple[CatalogOption, ...]
    source_url: str
    fetched_at: str
    stale: bool = False
    error: str = ""


def search_course_catalog(
    *,
    q: str = "",
    subject: str = "",
    course_number: str = "",
    section: str = "",
    instructor: str = "",
    open_only: bool = False,
    page: int = 1,
    page_size: int = CATALOG_PAGE_SIZE,
) -> CatalogSearchResult:
    filters = {
        "q": q.strip(),
        "subject": subject.strip().upper(),
        "course_number": course_number.strip(),
        "section": section.strip().upper(),
        "instructor": instructor.strip(),
        "open_only": "1" if open_only else "",
        "page": str(max(page, 1)),
        "page_size": str(page_size),
    }
    cache_path = _cache_path(filters)
    try:
        result = _fetch_course_catalog(filters, page_size=page_size)
        _write_cache(cache_path, result)
        return result
    except Exception as exc:
        cached = _read_cache(cache_path)
        if cached:
            return _result_from_payload(cached, stale=True, error=str(exc))
        raise CourseCatalogError(f"Could not load Kean Course Catalog data: {exc}") from exc


def _fetch_course_catalog(filters: dict[str, str], *, page_size: int) -> CatalogSearchResult:
    timeout = httpx.Timeout(30.0, connect=12.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        token = _get_antiforgery_token(client)
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "__RequestVerificationToken": token,
        }
        payload = _build_payload(filters, page_size=page_size)
        response = client.post(SEARCH_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    return _result_from_payload(data)


def _get_antiforgery_token(client: httpx.Client) -> str:
    response = client.get(COURSES_URL)
    response.raise_for_status()
    match = TOKEN_RE.search(response.text)
    if not match:
        raise CourseCatalogError("KeanWISE did not return an antiforgery token.")
    return match.group(1)


def _build_payload(filters: dict[str, str], *, page_size: int) -> dict[str, Any]:
    keyword_components = []
    if filters["subject"] or filters["course_number"] or filters["section"]:
        keyword_components.append(
            {
                "subject": filters["subject"],
                "courseNumber": filters["course_number"],
                "section": filters["section"],
                "synonym": "",
            }
        )
    return {
        "subjects": [filters["subject"]] if filters["subject"] and not keyword_components else [],
        "synonyms": [],
        "academicLevels": [],
        "courseLevels": [],
        "courseTypes": [],
        "topicCodes": [],
        "terms": [DEFAULT_TERM],
        "days": [],
        "locations": [DEFAULT_LOCATION],
        "faculty": [filters["instructor"]] if filters["instructor"] else [],
        "startDate": None,
        "endDate": None,
        "startTime": None,
        "endTime": None,
        "startsAtTime": None,
        "endsByTime": None,
        "keyword": filters["q"] or None,
        "requirement": None,
        "subrequirement": None,
        "group": None,
        "courseIds": None,
        "sectionIds": None,
        "requirementText": None,
        "subRequirementText": None,
        "onlineCategories": None,
        "pageNumber": max(int(filters["page"]), 1),
        "quantityPerPage": page_size,
        "openSections": True if filters["open_only"] else None,
        "openAndWaitlistedSections": None,
        "keywordComponents": keyword_components,
        "searchResultsView": "SectionListing",
        "sortOn": "None",
        "sortDirection": "Ascending",
    }


def _result_from_payload(payload: dict[str, Any], *, stale: bool = False, error: str = "") -> CatalogSearchResult:
    sections = tuple(_section_from_payload(section) for section in payload.get("Sections", []) if isinstance(section, dict))
    return CatalogSearchResult(
        sections=sections,
        total=_int(payload.get("TotalItems")),
        total_pages=_int(payload.get("TotalPages")),
        page=max(_int(payload.get("CurrentPageIndex")), 1) if payload.get("Sections") else _int(payload.get("CurrentPageIndex")),
        page_size=_int(payload.get("PageSize")) or CATALOG_PAGE_SIZE,
        subjects=_options_from_payload(payload.get("Subjects", [])),
        faculty=_options_from_payload(payload.get("Faculty", [])),
        locations=_options_from_payload(payload.get("Locations", [])),
        source_url=OFFICIAL_FALL_2026_WENZHOU_URL,
        fetched_at=str(payload.get("_fetched_at") or _now_iso()),
        stale=stale,
        error=error,
    )


def _section_from_payload(payload: dict[str, Any]) -> CatalogSection:
    course = payload.get("Course") if isinstance(payload.get("Course"), dict) else {}
    subject = clean_text(course.get("SubjectCode")) or _section_part(payload, 0)
    course_number = clean_text(course.get("Number")) or _section_part(payload, 1)
    section = clean_text(payload.get("Number")) or _section_part(payload, 2)
    return CatalogSection(
        id=clean_text(payload.get("Id")),
        course_id=clean_text(payload.get("CourseId")),
        subject=subject,
        course_number=course_number,
        section=section,
        title=clean_text(payload.get("Title")) or clean_text(course.get("Title")),
        term=clean_text(payload.get("TermDisplay")) or DEFAULT_TERM_NAME,
        location=clean_text(payload.get("LocationDisplay")) or DEFAULT_LOCATION_NAME,
        dates=clean_text(payload.get("DatesDisplay")),
        credits=_credits(payload.get("CreditsDisplay")),
        instructors=", ".join(clean_text(name) for name in payload.get("FacultyDisplay", []) if clean_text(name)),
        meetings=tuple(clean_text(value) for value in payload.get("MeetingsDisplay", []) if clean_text(value)),
        availability_status=clean_text(payload.get("AvailabilityStatusDisplay")),
        availability=clean_text(payload.get("AvailabilityDisplay")),
        available=_nullable_int(payload.get("Available")),
        capacity=_nullable_int(payload.get("Capacity")),
        enrolled=_nullable_int(payload.get("Enrolled")),
        waitlisted=_nullable_int(payload.get("Waitlisted")),
        methods=tuple(clean_text(value) for value in payload.get("InstructionalMethodsDisplay", []) if clean_text(value)),
        comments=clean_text(payload.get("Comments")),
        bookstore_url=clean_text(payload.get("BookstoreUrl")),
        description=clean_text(payload.get("CourseDescription")) or clean_text(course.get("Description")),
    )


def _section_part(payload: dict[str, Any], index: int) -> str:
    parts = clean_text(payload.get("SectionNameDisplay")).split("*")
    return parts[index].strip() if len(parts) > index else ""


def _options_from_payload(values: list[Any]) -> tuple[CatalogOption, ...]:
    options: list[CatalogOption] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        option_value = clean_text(value.get("Value") or value.get("Code") or value.get("Item1"))
        label = clean_text(value.get("Description") or value.get("Item2") or option_value)
        if option_value and label:
            options.append(CatalogOption(option_value, label, _nullable_int(value.get("Count"))))
    return tuple(options)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    return re.sub(r"\s+", " ", text.replace("\u202f", " ").replace("\xa0", " ")).strip()


def _credits(value: Any) -> str:
    text = clean_text(value)
    return re.sub(r"(\d+)\.0+\s+Credits", r"\1 Credits", text)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _nullable_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cache_path(filters: dict[str, str]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = json.dumps(filters, sort_keys=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def _write_cache(path: Path, result: CatalogSearchResult) -> None:
    payload = {
        "Sections": [_section_to_cache(section) for section in result.sections],
        "TotalItems": result.total,
        "TotalPages": result.total_pages,
        "CurrentPageIndex": result.page,
        "PageSize": result.page_size,
        "Subjects": [_option_to_cache(option) for option in result.subjects],
        "Faculty": [_option_to_cache(option) for option in result.faculty],
        "Locations": [_option_to_cache(option) for option in result.locations],
        "_fetched_at": result.fetched_at,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _section_to_cache(section: CatalogSection) -> dict[str, Any]:
    return {
        "Id": section.id,
        "CourseId": section.course_id,
        "Course": {"SubjectCode": section.subject, "Number": section.course_number, "Title": section.title},
        "Number": section.section,
        "Title": section.title,
        "TermDisplay": section.term,
        "LocationDisplay": section.location,
        "DatesDisplay": section.dates,
        "CreditsDisplay": section.credits,
        "FacultyDisplay": list(section.instructors.split(", ")) if section.instructors else [],
        "MeetingsDisplay": list(section.meetings),
        "AvailabilityStatusDisplay": section.availability_status,
        "AvailabilityDisplay": section.availability,
        "Available": section.available,
        "Capacity": section.capacity,
        "Enrolled": section.enrolled,
        "Waitlisted": section.waitlisted,
        "InstructionalMethodsDisplay": list(section.methods),
        "Comments": section.comments,
        "BookstoreUrl": section.bookstore_url,
        "CourseDescription": section.description,
    }


def _option_to_cache(option: CatalogOption) -> dict[str, Any]:
    return {"Value": option.value, "Description": option.label, "Count": option.count}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
