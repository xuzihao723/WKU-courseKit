import threading
from datetime import date, timedelta
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

from app.database import SessionLocal, init_db
from app.i18n import (
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
    format_date,
    format_short_date,
    language_url,
    path_with_language,
    resolve_language,
    translate,
    translate_course_title,
    translate_status,
)
from app.services.ui_data import (
    DEMO_STUDENT_KEY,
    count_library_results,
    get_course,
    get_course_results,
    get_courses_by_identity_keys,
    get_instructors,
    get_library_results,
    get_latest_enrolled_term_code,
    get_latest_term_code,
    get_material_rows,
    get_subjects,
    get_terms,
    group_course_sections,
    parse_has_materials,
)
from app.services.simple_syllabus import (
    SimpleSyllabusImportError,
    official_links,
)
from app.services.material_service import legal_source_links
from app.services.course_catalog import (
    CATALOG_PAGE_SIZE,
    CourseCatalogError,
    DEFAULT_TERM_NAME,
    DEFAULT_LOCATION_NAME,
    search_course_catalog,
)
from app.services.simple_syllabus_scraper import (
    build_library_search_filters,
    clear_simple_syllabus_session,
    current_simple_syllabus_auth_snapshot,
    facets_have_term_ids,
    fetch_library_facets_with_session,
    read_library_filter_facets,
    read_library_page_cache,
    read_library_search_meta,
    sync_course_detail_from_saved_session,
    sync_from_logged_in_browser,
    sync_library_page_from_saved_session,
    sync_material_counts_from_saved_session,
    write_library_search_meta_from_responses,
    write_simple_syllabus_auth_status,
    normalize_scraped_responses,
)
from app.services.importer import load_syllabus_payload
from app.services.syllabus_view import (
    assessment_rows,
    course_catalog_description,
    course_detail_sections,
    course_meeting_time,
    grade_rows,
    localized_section_heading,
    localized_labeled_body_lines,
    localized_syllabus_body,
    section_kind,
    topic_table,
)
from app.templating import templates


router = APIRouter()
LIBRARY_PAGE_SIZE = 50
auth_sync_thread: threading.Thread | None = None


def start_simple_syllabus_background_sync() -> bool:
    global auth_sync_thread
    if auth_sync_thread is not None and auth_sync_thread.is_alive():
        return False
    write_simple_syllabus_auth_status(
        "running",
        "Kean Simple Syllabus sync is running in the background. Local pages update as batches are imported.",
    )
    auth_sync_thread = threading.Thread(
        target=run_simple_syllabus_background_sync,
        name="kean-simple-syllabus-sync",
        daemon=True,
    )
    auth_sync_thread.start()
    return True


def run_simple_syllabus_background_sync() -> None:
    try:
        init_db()
        with SessionLocal() as db:
            sync_from_logged_in_browser(db, reset=True)
    except SimpleSyllabusImportError as exc:
        write_simple_syllabus_auth_status("error", str(exc))
    except Exception as exc:
        write_simple_syllabus_auth_status(
            "error",
            f"Could not refresh Kean Simple Syllabus login. {type(exc).__name__}: {exc}",
        )


def auth_snapshot() -> dict[str, object]:
    snapshot = current_simple_syllabus_auth_snapshot()
    running = auth_sync_thread is not None and auth_sync_thread.is_alive()
    snapshot["running"] = running
    if running:
        snapshot["signed_in"] = False
    return snapshot


def sync_library_page_from_saved_kean_session(
    *,
    page_number: int,
    page_size: int,
    filters: dict[str, str] | None,
    reset: bool = False,
) -> None:
    with SessionLocal() as db:
        sync_library_page_from_saved_session(
            db,
            page_number=page_number,
            page_size=page_size,
            filters=filters,
            reset=reset,
        )


@router.get("/health")
def health_check() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@router.get("/")
def home(request: Request) -> RedirectResponse:
    language = resolve_language(request)
    destination = path_with_language("/courses", language) if request.query_params.get("lang") in SUPPORTED_LANGUAGES else "/courses"
    response = RedirectResponse(destination, status_code=303)
    if request.query_params.get("lang") in SUPPORTED_LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE,
            language,
            max_age=60 * 60 * 24 * 180,
            samesite="lax",
        )
    return response


@router.get("/auth/status")
def simple_syllabus_auth_status() -> JSONResponse:
    return JSONResponse(auth_snapshot())


@router.get("/api/material-counts")
def material_counts(ids: str = Query(default="")) -> JSONResponse:
    course_ids: list[int] = []
    for raw_id in ids.split(","):
        raw_id = raw_id.strip()
        if raw_id.isdigit():
            course_ids.append(int(raw_id))
    with SessionLocal() as db:
        counts = sync_material_counts_from_saved_session(db, course_ids)
        db.commit()
    return JSONResponse({"counts": counts})


@router.post("/api/simple-syllabus/import-responses")
async def import_simple_syllabus_responses(request: Request) -> JSONResponse:
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SimpleSyllabusImportError("Uploaded Simple Syllabus browser data is not valid JSON.") from exc

    responses = payload.get("responses") if isinstance(payload, dict) else None
    if not isinstance(responses, list):
        raise SimpleSyllabusImportError("Simple Syllabus browser data must include a responses array.")
    normalized_responses = [
        {"url": str(item.get("url", "")), "payload": item.get("payload")}
        for item in responses
        if isinstance(item, dict) and isinstance(item.get("payload"), dict)
    ]
    if not normalized_responses:
        raise SimpleSyllabusImportError("No usable Simple Syllabus JSON responses were submitted.")

    with SessionLocal() as db:
        write_library_search_meta_from_responses(normalized_responses)
        syllabus_payload = normalize_scraped_responses(normalized_responses)
        counts = load_syllabus_payload(db, syllabus_payload, reset=True)
    course_count = sum(len(term.get("courses", [])) for term in syllabus_payload.get("terms", []))
    write_simple_syllabus_auth_status(
        "success",
        "Imported browser-submitted Simple Syllabus data.",
        response_count=len(normalized_responses),
        course_count=course_count,
    )
    return JSONResponse({"status": "success", "counts": counts, "response_count": len(normalized_responses), "course_count": course_count})


@router.post("/auth/refresh")
def refresh_simple_syllabus_auth(
    next_url: str = Form(default="/courses"),
) -> RedirectResponse:
    if not next_url.startswith("/"):
        next_url = "/courses"
    separator = "&" if "?" in next_url else "?"
    started = start_simple_syllabus_background_sync()
    status = "started" if started else "running"
    return RedirectResponse(f"{next_url}{separator}sync={status}", status_code=303)


@router.post("/auth/logout")
def logout_simple_syllabus_auth(
    next_url: str = Form(default="/courses"),
) -> RedirectResponse:
    if not next_url.startswith("/"):
        next_url = "/courses"
    clear_simple_syllabus_session()
    separator = "&" if "?" in next_url else "?"
    return RedirectResponse(f"{next_url}{separator}auth=logged_out", status_code=303)


@router.get("/courses", response_class=HTMLResponse)
def my_courses(
    request: Request,
    q: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: str | None = Query(default=None),
    sort: str = "course_code",
) -> HTMLResponse:
    with SessionLocal() as db:
        active_term = term
        if not any([q, term, subject, has_materials]):
            active_term = get_latest_enrolled_term_code(db)
        courses = get_course_results(
            db,
            q=q,
            term=active_term,
            subject=subject,
            has_materials=parse_has_materials(has_materials),
            sort=sort,
        )
        return render(
            request,
            "pages/courses.html",
            page={"title_key": "nav.courses", "eyebrow_en": "Enrollment", "eyebrow_zh": "选课"},
            active_path="/courses",
            courses=courses,
            course_groups=group_course_sections(courses),
            terms=get_terms(db),
            subjects=get_subjects(db),
            filters={
                "q": q or "",
                "term": active_term or "",
                "subject": subject or "",
                "has_materials": has_materials or "",
                "sort": sort,
            },
        )


@router.get("/library", response_class=HTMLResponse)
def syllabus_library(
    request: Request,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    has_materials: str | None = Query(default=None),
    sort: str = "course_code",
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    import_error = None
    synced_remote_pages: set[int] = set()
    with SessionLocal() as db:
        active_term = term
        if term is None and not any([q, course_code, title, instructor, subject, has_materials]):
            active_term = default_library_term_code(db)
        has_materials_filter = parse_has_materials(has_materials)
        filters_are_empty = not any([q, course_code, title, instructor, active_term, subject, has_materials])
        if active_term and not facets_have_term_ids(read_library_filter_facets()):
            try:
                fetch_library_facets_with_session([], set())
            except Exception:
                pass
        remote_filters = build_library_search_filters(
            q=q,
            course_code=course_code,
            title=title,
            instructor=instructor,
            term=active_term,
            subject=subject,
        )
        can_remote_search = filters_are_empty or bool(remote_filters)
        local_total = count_library_results(
            db,
            q=q,
            course_code=course_code,
            title=title,
            instructor=instructor,
            term=active_term,
            subject=subject,
            has_materials=has_materials_filter,
        )
        meta = read_library_search_meta(remote_filters) if can_remote_search else {}
        if can_remote_search and not meta:
            try:
                sync_library_page_from_saved_kean_session(
                    page_number=0,
                    page_size=LIBRARY_PAGE_SIZE,
                    filters=remote_filters,
                    reset=False,
                )
                synced_remote_pages.add(0)
                db.expire_all()
                local_total = count_library_results(
                    db,
                    q=q,
                    course_code=course_code,
                    title=title,
                    instructor=instructor,
                    term=active_term,
                    subject=subject,
                    has_materials=has_materials_filter,
                )
                meta = read_library_search_meta(remote_filters)
            except SimpleSyllabusImportError as exc:
                import_error = str(exc)
            except Exception as exc:
                import_error = f"Could not load Kean Syllabus Library results: {exc}"
        total = meta.get("total", 0) if can_remote_search and meta.get("total") else local_total
        local_pages = max(1, (local_total + LIBRARY_PAGE_SIZE - 1) // LIBRARY_PAGE_SIZE)
        pagination = build_pagination(request, total=total, page=page, page_size=LIBRARY_PAGE_SIZE)
        remote_page_number = int(pagination["page"]) - 1
        use_remote_page_cache = can_remote_search and filters_are_empty and has_materials_filter is None
        page_cache = read_library_page_cache(filters=remote_filters, page_number=remote_page_number) if use_remote_page_cache else []
        cached_courses = get_courses_by_identity_keys(db, page_cache) if page_cache else []
        expected_rows = max(0, int(pagination["end"]) - int(pagination["start"]) + 1) if int(pagination["start"]) else 0
        cache_is_usable = bool(cached_courses) and (not expected_rows or len(cached_courses) >= min(expected_rows, len(page_cache)))
        local_page_has_rows = local_total >= int(pagination["start"]) if int(pagination["start"]) else local_total > 0
        should_fetch_remote_page = can_remote_search and not cache_is_usable and not local_page_has_rows
        if can_remote_search and should_fetch_remote_page and not cache_is_usable and remote_page_number not in synced_remote_pages:
            try:
                sync_library_page_from_saved_kean_session(
                    page_number=remote_page_number,
                    page_size=LIBRARY_PAGE_SIZE,
                    filters=remote_filters,
                    reset=False,
                )
                synced_remote_pages.add(remote_page_number)
                db.expire_all()
                local_total = count_library_results(
                    db,
                    q=q,
                    course_code=course_code,
                    title=title,
                    instructor=instructor,
                    term=active_term,
                    subject=subject,
                    has_materials=has_materials_filter,
                )
                meta = read_library_search_meta(remote_filters)
                total = meta.get("total", 0) if meta.get("total") else local_total
                pagination = build_pagination(request, total=total, page=page, page_size=LIBRARY_PAGE_SIZE)
                remote_page_number = int(pagination["page"]) - 1
                page_cache = read_library_page_cache(filters=remote_filters, page_number=remote_page_number) if use_remote_page_cache else []
                cached_courses = get_courses_by_identity_keys(db, page_cache) if page_cache else []
            except SimpleSyllabusImportError as exc:
                import_error = str(exc)
            except Exception as exc:
                import_error = f"Could not load that Kean Syllabus Library page: {exc}"
        courses = cached_courses if use_remote_page_cache else []
        if not courses:
            courses = get_library_results(
                db,
                q=q,
                course_code=course_code,
                title=title,
                instructor=instructor,
                term=active_term,
                subject=subject,
                has_materials=has_materials_filter,
                sort=sort,
                limit=LIBRARY_PAGE_SIZE,
                offset=int(pagination["offset"]),
            )
        return render(
            request,
            "pages/library.html",
            page={"title_key": "nav.library", "eyebrow_key": "library.eyebrow"},
            active_path="/library",
            courses=courses,
            course_groups=group_course_sections(courses),
            terms=library_filter_terms(db),
            subjects=library_filter_subjects(db),
            instructors=get_instructors(db),
            pagination=pagination,
            import_error=import_error,
            filters={
                "q": q or "",
                "course_code": course_code or "",
                "title": title or "",
                "instructor": instructor or "",
                "term": active_term or "",
                "subject": subject or "",
                "has_materials": has_materials or "",
                "sort": sort,
                "page": pagination["page"],
            },
        )


@router.get("/catalog", response_class=HTMLResponse)
def course_catalog(
    request: Request,
    q: str | None = None,
    subject: str | None = None,
    course_number: str | None = None,
    section: str | None = None,
    instructor: str | None = None,
    open_only: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    import_error = None
    catalog = None
    facet_source = None
    filters = {
        "q": q or "",
        "subject": (subject or "").upper(),
        "course_number": course_number or "",
        "section": (section or "").upper(),
        "instructor": instructor or "",
        "open_only": open_only or "",
    }
    try:
        catalog = search_course_catalog(
            q=filters["q"],
            subject=filters["subject"],
            course_number=filters["course_number"],
            section=filters["section"],
            instructor=filters["instructor"],
            open_only=open_only == "yes",
            page=page,
            page_size=CATALOG_PAGE_SIZE,
        )
        has_active_filter = any(value for key, value in filters.items() if key != "open_only") or open_only == "yes" or page != 1
        if has_active_filter:
            try:
                facet_source = search_course_catalog(page=1, page_size=CATALOG_PAGE_SIZE)
            except CourseCatalogError:
                facet_source = catalog
        else:
            facet_source = catalog
    except CourseCatalogError as exc:
        import_error = str(exc)
    total = catalog.total if catalog else 0
    return render(
        request,
        "pages/catalog.html",
        page={"title_key": "nav.catalog", "eyebrow_key": "catalog.eyebrow"},
        active_path="/catalog",
        catalog=catalog,
        sections=catalog.sections if catalog else [],
        subjects=facet_source.subjects if facet_source else [],
        faculty=facet_source.faculty if facet_source else [],
        pagination=build_pagination(request, total=total, page=page, page_size=CATALOG_PAGE_SIZE),
        import_error=import_error,
        catalog_term=DEFAULT_TERM_NAME,
        catalog_location=DEFAULT_LOCATION_NAME,
        filters={**filters, "page": page},
    )


@router.get("/simple-syllabus")
def removed_simple_syllabus_page() -> RedirectResponse:
    return RedirectResponse("/courses", status_code=303)


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_detail(request: Request, course_id: int) -> HTMLResponse:
    with SessionLocal() as db:
        course = get_course(db, course_id)
        if course and course.simple_syllabus_doc_code:
            try:
                sync_course_detail_from_saved_session(db, course)
                db.expire_all()
                course = get_course(db, course_id)
            except SimpleSyllabusImportError:
                db.rollback()
            except Exception:
                db.rollback()
        return render(
            request,
            "pages/course_detail.html",
            page={"title": "Course Detail", "title_zh": "课程详情", "eyebrow_key": "course_detail.eyebrow"},
            active_path="/courses",
            course=course,
        )


@router.get("/materials", response_class=HTMLResponse)
def materials(
    request: Request,
    q: str | None = None,
    term: str | None = None,
    subject: str | None = None,
    requirement: str | None = None,
) -> HTMLResponse:
    with SessionLocal() as db:
        rows = get_material_rows(
            db,
            q=q,
            term=term,
            subject=subject,
            requirement=requirement,
        )
        return render(
            request,
            "pages/materials.html",
            page={"title_key": "nav.materials", "eyebrow_key": "materials.eyebrow"},
            active_path="/materials",
            rows=rows,
            terms=get_terms(db),
            subjects=get_subjects(db),
            filters={
                "q": q or "",
                "term": term or "",
                "subject": subject or "",
                "requirement": requirement or "",
            },
        )


def build_pagination(request: Request, *, total: int, page: int, page_size: int) -> dict[str, object]:
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(max(page, 1), total_pages)
    offset = (current_page - 1) * page_size
    start = offset + 1 if total else 0
    end = min(offset + page_size, total)

    def page_url(page_number: int) -> str:
        params = dict(request.query_params)
        params["page"] = str(page_number)
        return f"{request.url.path}?{urlencode(params)}"

    window = page_window(current_page, total_pages)
    pages = [
        {
            "type": "ellipsis" if number is None else "page",
            "number": number,
            "url": page_url(number) if number is not None else "",
            "current": number == current_page,
        }
        for number in window
    ]
    return {
        "page": current_page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "offset": offset,
        "start": start,
        "end": end,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_url": page_url(current_page - 1) if current_page > 1 else "",
        "next_url": page_url(current_page + 1) if current_page < total_pages else "",
        "pages": pages,
    }


def page_window(current_page: int, total_pages: int) -> list[int | None]:
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    candidates = {1, total_pages, current_page - 1, current_page, current_page + 1}
    if current_page <= 3:
        candidates.update({2, 3, 4})
    if current_page >= total_pages - 2:
        candidates.update({total_pages - 3, total_pages - 2, total_pages - 1})

    numbers = sorted(number for number in candidates if 1 <= number <= total_pages)
    window: list[int | None] = []
    previous = 0
    for number in numbers:
        if previous and number - previous > 1:
            window.append(None)
        window.append(number)
        previous = number
    return window


def library_filter_terms(db) -> list[object]:
    facets = read_library_filter_facets()
    local_terms = get_terms(db)
    merged: list[object] = []
    seen: set[str] = set()
    for term in facets.get("terms", []):
        code = str(term.get("code", "")).strip() if isinstance(term, dict) else ""
        if code and code not in seen:
            merged.append({"code": code, "name": str(term.get("name") or code)})
            seen.add(code)
    for term in local_terms:
        if term.code not in seen:
            merged.append(term)
            seen.add(term.code)
    return merged


def default_library_term_code(db) -> str | None:
    facets = read_library_filter_facets()
    cutoff = date.today() + timedelta(days=45)
    for term in facets.get("terms", []):
        if not isinstance(term, dict):
            continue
        start_date = parse_iso_date(str(term.get("start_date") or ""))
        if start_date and start_date <= cutoff:
            return str(term.get("code") or "")
    return get_latest_term_code(db)


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def library_filter_subjects(db) -> list[str]:
    facets = read_library_filter_facets()
    subjects = {subject for subject in get_subjects(db) if subject}
    subjects.update(str(subject).strip().upper() for subject in facets.get("subjects", []) if str(subject).strip())
    return sorted(subjects)


@router.get("/print", response_class=HTMLResponse)
def print_center(
    request: Request,
    term: str | None = None,
) -> HTMLResponse:
    with SessionLocal() as db:
        courses = get_course_results(db, term=term, sort="course_code")
        rows = get_material_rows(db, term=term)
        return render(
            request,
            "pages/print_center.html",
            page={"title_key": "nav.print", "eyebrow_key": "print.eyebrow"},
            active_path="/print",
            courses=courses,
            rows=rows,
            terms=get_terms(db),
            filters={"term": term or ""},
        )


def render(
    request: Request,
    template_name: str,
    *,
    page: dict[str, str],
    active_path: str,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    language = resolve_language(request)

    def t(key: str) -> str:
        return translate(language, key)

    def nav(path: str) -> str:
        return path_with_language(path, language)

    def switch(language_code: str) -> str:
        return language_url(request, language_code)

    current_path = request.url.path
    if request.url.query:
        current_path = f"{current_path}?{request.url.query}"

    def page_text(field: str) -> str:
        keyed = page.get(f"{field}_key")
        if keyed:
            return t(keyed)
        localized = page.get(f"{field}_{language}")
        if localized:
            return localized
        return page.get(field, "")

    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        status_code=status_code,
        context={
            "page": page,
            "page_title": page_text("title"),
            "page_eyebrow": page_text("eyebrow"),
            "active_path": active_path,
            "lang": language,
            "languages": SUPPORTED_LANGUAGES,
            "t": t,
            "ts": lambda value: translate_status(language, value),
            "course_title": lambda value: translate_course_title(language, value),
            "fmt_date": lambda value: format_date(value, language),
            "fmt_short_date": lambda value: format_short_date(value, language),
            "course_time": course_meeting_time,
            "course_catalog_description": course_catalog_description,
            "course_detail_sections": course_detail_sections,
            "localized_section_heading": localized_section_heading,
            "localized_labeled_body_lines": localized_labeled_body_lines,
            "localized_syllabus_body": localized_syllabus_body,
            "legal_source_links": legal_source_links,
            "section_kind": section_kind,
            "topic_table": topic_table,
            "assessment_rows": assessment_rows,
            "grade_rows": grade_rows,
            "nav_url": nav,
            "switch_lang_url": switch,
            "current_path": current_path,
            "sync_status": request.query_params.get("sync", ""),
            "auth": auth_snapshot(),
            "official_links": official_links(),
            **context,
        },
    )
    if request.query_params.get("lang") in SUPPORTED_LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE,
            language,
            max_age=60 * 60 * 24 * 180,
            samesite="lax",
        )
    return response
