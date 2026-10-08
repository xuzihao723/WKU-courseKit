from __future__ import annotations

import json
import os
import re
import base64
import shutil
import webbrowser
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from app.models import Course
from app.services.importer import load_syllabus_payload
from app.services.simple_syllabus import (
    SIMPLE_SYLLABUS_LIBRARY_URL,
    SIMPLE_SYLLABUS_MY_COURSES_URL,
    SimpleSyllabusImportError,
)


COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s*[- ]?\s*(\d{3,4}[A-Z]?)\b", re.IGNORECASE)
SECTION_CODE_PATTERN = r"[A-Z]{0,4}\d{1,4}[A-Z]{0,3}"
COURSE_LISTING_RE = re.compile(rf"\b([A-Z]{{2,6}})\s+(\d{{3,4}}[A-Z]?)[\s-]+({SECTION_CODE_PATTERN})\b", re.IGNORECASE)
TERM_CODE_RE = re.compile(r"\b(20\d{2}\s?(?:SP|FA|SU|WI|SPRING|FALL|SUMMER|WINTER))\b", re.IGNORECASE)
ISBN_RE = re.compile(r"(?:97[89])?\d[\d\-\s]{8,17}[\dXx]")
DOC_VIEW_RE = re.compile(r"/doc/([^/?#]+)/([^/?#]+)", re.IGNORECASE)
COURSE_SLUG_RE = re.compile(
    rf"\b(?P<term>20\d{{2}}[A-Z]{{2,5}})-(?P<subject>[A-Z]{{2,6}})-(?P<number>\d{{3,4}}[A-Z]?)-(?P<section>{SECTION_CODE_PATTERN})\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

SYSTEM_SECTION_MARKERS = (
    "system instructor role",
    "built-in functionality: edit syllabi",
    "populate in instructor blocks",
    "doc status report",
)
SKIPPED_SECTION_HEADINGS = {
    "administrator",
    "author",
    "course",
    "default",
    "instructor",
    "instructors",
    "logo",
    "role",
    "roles",
    "section",
    "session",
    "student",
    "term",
    "user",
}
MATERIAL_KEYWORDS = (
    "isbn",
    "book",
    "textbook",
    "material",
    "publisher",
    "edition",
    "required",
    "optional",
)

_browser_sync_playwright: Any | None = None
_browser_sync_context: Any | None = None
_browser_sync_capture: dict[str, Any] | None = None
_simple_syllabus_app_version: str | None = None


@dataclass(frozen=True)
class BrowserSyncResult:
    counts: dict[str, int]
    response_count: int
    course_count: int


def write_simple_syllabus_auth_status(
    status: str,
    message: str,
    *,
    response_count: int | None = None,
    course_count: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "message": message,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    if response_count is not None:
        payload["response_count"] = response_count
    if course_count is not None:
        payload["course_count"] = course_count
    try:
        simple_syllabus_auth_status_path().write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def current_simple_syllabus_auth_snapshot() -> dict[str, Any]:
    try:
        payload = json.loads(simple_syllabus_auth_status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {
            "status": "idle",
            "message": "Kean Simple Syllabus login has not been refreshed in this app session.",
            "updated_at": None,
        }
    payload["profile_ready"] = browser_profile_dir().exists()
    session = read_simple_syllabus_session()
    payload["session_ready"] = bool(session.get("cookie") or session.get("bearer_token"))
    payload["session_updated_at"] = session.get("updated_at")
    payload["account_name"] = session.get("account_name", "")
    payload["account_email"] = session.get("account_email", "")
    payload["signed_in"] = payload.get("status") == "success" or payload["session_ready"]
    return payload


def simple_syllabus_auth_status_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / "simple_syllabus_auth_status.json"


def simple_syllabus_session_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".simple_syllabus_session.json"


def save_simple_syllabus_session_from_context(context: Any) -> None:
    try:
        cookies = context.cookies(["https://kean.simplesyllabus.com"])
    except Exception:
        cookies = []
    cookie_header = format_cookie_header(cookies)
    token = find_simple_syllabus_token(context)
    if not cookie_header and not token:
        return
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    existing = read_simple_syllabus_session()
    payload = {
        "cookie": cookie_header,
        "bearer_token": token,
        "updated_at": updated_at,
        "account_name": existing.get("account_name", ""),
        "account_email": existing.get("account_email", ""),
        "account_id": existing.get("account_id", ""),
    }
    update_env_value("SIMPLE_SYLLABUS_COOKIE", cookie_header)
    update_env_value("SIMPLE_SYLLABUS_BEARER_TOKEN", token)
    update_env_value("SIMPLE_SYLLABUS_SESSION_UPDATED_AT", updated_at)
    try:
        simple_syllabus_session_path().write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def read_simple_syllabus_session() -> dict[str, Any]:
    env_values = read_env_values()
    try:
        file_payload = json.loads(simple_syllabus_session_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        file_payload = {}
    if not isinstance(file_payload, dict):
        file_payload = {}
    session = {
        "cookie": env_values.get("SIMPLE_SYLLABUS_COOKIE", "") or file_payload.get("cookie", ""),
        "bearer_token": env_values.get("SIMPLE_SYLLABUS_BEARER_TOKEN", "") or file_payload.get("bearer_token", ""),
        "updated_at": env_values.get("SIMPLE_SYLLABUS_SESSION_UPDATED_AT", "") or file_payload.get("updated_at", ""),
        "account_name": file_payload.get("account_name", ""),
        "account_email": file_payload.get("account_email", ""),
        "account_id": file_payload.get("account_id", ""),
    }
    if session["cookie"] or session["bearer_token"]:
        return session
    return file_payload


def clear_simple_syllabus_session() -> None:
    reset_browser_sync_context()
    for key in (
        "SIMPLE_SYLLABUS_COOKIE",
        "SIMPLE_SYLLABUS_BEARER_TOKEN",
        "SIMPLE_SYLLABUS_SESSION_UPDATED_AT",
    ):
        update_env_value(key, "")
    try:
        simple_syllabus_session_path().unlink(missing_ok=True)
    except OSError:
        pass
    for profile_dir in browser_profile_dirs():
        try:
            resolved = profile_dir.resolve()
            if resolved.name.startswith(".wkcoursekit-simple-syllabus-browser") and resolved.exists():
                shutil.rmtree(resolved)
        except OSError:
            continue
    write_simple_syllabus_auth_status(
        "logged_out",
        "Kean Simple Syllabus session was cleared. Sign in again to refresh data.",
    )


def env_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".env"


def read_env_values() -> dict[str, str]:
    try:
        lines = env_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        values[key] = str(value)
    return values


def update_env_value(key: str, value: str | None) -> None:
    if value is None:
        value = ""
    path = env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    replacement = f"{key}={json.dumps(value, ensure_ascii=False)}"
    updated = False
    output: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            output.append(replacement)
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(replacement)
    try:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    except OSError:
        return


def find_simple_syllabus_token(context: Any) -> str:
    for page in reversed(list(context.pages)):
        try:
            token = page.evaluate(
                """() => {
                    const stores = [window.localStorage, window.sessionStorage].filter(Boolean);
                    const candidates = [];
                    for (const store of stores) {
                        for (let index = 0; index < store.length; index += 1) {
                            const key = store.key(index);
                            const value = store.getItem(key);
                            if (!value) continue;
                            const text = String(value);
                            if (/token|jwt|bearer|auth/i.test(String(key)) || /^eyJ[A-Za-z0-9_-]+\\./.test(text)) {
                                candidates.push(text.replace(/^Bearer\\s+/i, ''));
                            }
                        }
                    }
                    return candidates.find((value) => /^eyJ[A-Za-z0-9_-]+\\./.test(value)) || candidates[0] || '';
                }"""
            )
        except Exception:
            token = ""
        if token:
            return str(token)
    return ""


def format_cookie_header(cookies: list[dict[str, Any]]) -> str:
    pairs = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def open_simple_syllabus_in_default_browser() -> None:
    webbrowser.open(SIMPLE_SYLLABUS_LIBRARY_URL, new=2, autoraise=True)


def sync_from_logged_in_browser(db: Session, *, reset: bool = True, timeout_seconds: int = 180) -> BrowserSyncResult:
    write_simple_syllabus_auth_status(
        "waiting_login",
        "Opening Kean Simple Syllabus and waiting for an authenticated browser session.",
    )
    try:
        result = capture_and_import_simple_syllabus_json(db, reset=reset, timeout_seconds=timeout_seconds)
    except SimpleSyllabusImportError as exc:
        write_simple_syllabus_auth_status(
            "error",
            str(exc),
        )
        raise
    except Exception as exc:
        forget_browser_sync_context()
        detail = f"{type(exc).__name__}: {exc}"
        write_simple_syllabus_auth_status(
            "error",
            f"Could not capture Kean Simple Syllabus data from the logged-in browser. {detail}",
        )
        raise SimpleSyllabusImportError("Could not capture Kean Simple Syllabus data from the logged-in browser.") from exc
    write_simple_syllabus_auth_status(
        "success",
        "Kean Simple Syllabus login refreshed and course data imported.",
        response_count=result.response_count,
        course_count=result.course_count,
    )
    return result


def capture_and_import_simple_syllabus_json(
    db: Session,
    *,
    reset: bool = True,
    timeout_seconds: int = 180,
) -> BrowserSyncResult:
    global _browser_sync_capture
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SimpleSyllabusImportError("Playwright is not installed. Run python -m pip install -r requirements.txt.") from exc

    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    started = time.monotonic()
    result: BrowserSyncResult | None = None
    should_reset = reset

    _browser_sync_capture = {"responses": responses, "seen": seen}
    context = get_or_launch_browser_context(sync_playwright)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(timeout_seconds * 1_000)
        try:
            page.goto(SIMPLE_SYLLABUS_MY_COURSES_URL, wait_until="domcontentloaded", timeout=60_000)
            try_select_historic_filter(page)
            wait_for_responses(page, responses, started, timeout_seconds, require_details=False)
            save_simple_syllabus_session_from_context(context)
            page = active_simple_syllabus_page(context) or page
            fetch_full_my_courses_search(page, responses, seen)
            fetch_materials_for_course_list_with_page(page, responses, seen, my_courses_only=True)
            if course_list_items(responses):
                result = import_scraped_responses(
                    db,
                    responses,
                    reset=should_reset,
                    message="Imported My Courses list. Course details load when you open a course.",
                )
                should_reset = False
            page = active_simple_syllabus_page(context) or page
            page.goto(SIMPLE_SYLLABUS_LIBRARY_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            save_simple_syllabus_session_from_context(context)
            fetch_library_search_page(page, responses, seen, page_number=0, page_size=50)
            if course_list_items(responses):
                result = import_scraped_responses(
                    db,
                    responses,
                    reset=should_reset,
                    message="Imported the first Syllabus Library page. More pages load on demand.",
                )
        except PlaywrightTimeoutError as exc:
            raise SimpleSyllabusImportError("Timed out while waiting for Kean Simple Syllabus data to load.") from exc
    finally:
        _browser_sync_capture = None

    if not responses:
        raise SimpleSyllabusImportError(
            "No Simple Syllabus JSON responses were captured. Sign in in the opened browser window, then run automatic sync again."
        )
    if result is None:
        result = import_scraped_responses(
            db,
            responses,
            reset=reset,
            message="Imported captured Simple Syllabus responses.",
        )
    minimize_browser_context(context)
    return result


def import_scraped_responses(
    db: Session,
    responses: list[dict[str, Any]],
    *,
    reset: bool,
    message: str,
) -> BrowserSyncResult:
    if reset:
        clear_library_search_meta()
    save_simple_syllabus_account_from_responses(responses)
    write_library_search_meta_from_responses(responses)
    payload = normalize_scraped_responses(responses)
    counts = load_syllabus_payload(db, payload, reset=reset)
    db.commit()
    result = BrowserSyncResult(
        counts=counts,
        response_count=len(responses),
        course_count=sum(len(term.get("courses", [])) for term in payload.get("terms", [])),
    )
    write_simple_syllabus_auth_status(
        "running",
        message,
        response_count=result.response_count,
        course_count=result.course_count,
    )
    return result


def save_simple_syllabus_account_from_responses(responses: list[dict[str, Any]]) -> None:
    account = account_from_responses(responses)
    if not account:
        return
    session = read_simple_syllabus_session()
    session.update(account)
    try:
        simple_syllabus_session_path().write_text(
            json.dumps(session, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def account_from_responses(responses: list[dict[str, Any]]) -> dict[str, str]:
    for response in responses:
        if "/api/session" not in str(response.get("url", "")):
            continue
        payload = response.get("payload")
        if not isinstance(payload, dict):
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        account = data.get("account") if isinstance(data.get("account"), dict) else {}
        full_name = str(account.get("full_name") or "").strip()
        first_name = str(account.get("first_name") or "").strip()
        last_name = str(account.get("last_name") or "").strip()
        display_name = full_name or " ".join(part for part in (first_name, last_name) if part).strip()
        if display_name:
            return {
                "account_name": display_name,
                "account_email": str(account.get("email") or "").strip(),
                "account_id": str(account.get("entity_id") or "").strip(),
            }
    return {}


def sync_library_page_from_logged_in_browser(
    db: Session,
    *,
    page_number: int,
    page_size: int = 50,
    filters: dict[str, str] | None = None,
    reset: bool = False,
    timeout_seconds: int = 60,
) -> BrowserSyncResult:
    write_simple_syllabus_auth_status(
        "running",
        f"Loading Syllabus Library page {page_number + 1} from Kean Simple Syllabus.",
    )
    try:
        responses: list[dict[str, Any]] = []
        seen: set[str] = set()
        if read_simple_syllabus_session().get("cookie"):
            fetch_library_search_page_with_session(
                responses,
                seen,
                page_number=page_number,
                page_size=page_size,
                filters=filters,
            )
        if not responses:
            responses = capture_simple_syllabus_library_page(
                page_number=page_number,
                page_size=page_size,
                filters=filters,
                timeout_seconds=timeout_seconds,
            )
    except SimpleSyllabusImportError as exc:
        write_simple_syllabus_auth_status(
            "error",
            str(exc),
        )
        raise
    except Exception as exc:
        forget_browser_sync_context()
        detail = f"{type(exc).__name__}: {exc}"
        write_simple_syllabus_auth_status(
            "error",
            f"Could not load Syllabus Library page {page_number + 1}. {detail}",
        )
        raise SimpleSyllabusImportError("Could not load that Kean Syllabus Library page from the logged-in browser.") from exc
    write_library_search_meta_from_responses(responses)
    payload = normalize_scraped_responses(responses)
    counts = load_syllabus_payload(db, payload, reset=reset)
    db.commit()
    result = BrowserSyncResult(
        counts=counts,
        response_count=len(responses),
        course_count=sum(len(term.get("courses", [])) for term in payload.get("terms", [])),
    )
    write_simple_syllabus_auth_status(
        "success",
        f"Loaded Syllabus Library page {page_number + 1}.",
        response_count=result.response_count,
        course_count=result.course_count,
    )
    return result


def sync_library_page_from_saved_session(
    db: Session,
    *,
    page_number: int,
    page_size: int = 50,
    filters: dict[str, str] | None = None,
    reset: bool = False,
) -> BrowserSyncResult:
    write_simple_syllabus_auth_status(
        "running",
        f"Loading Syllabus Library page {page_number + 1} with the saved Kean session.",
    )
    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    fetch_library_search_page_with_session(
        responses,
        seen,
        page_number=page_number,
        page_size=page_size,
        filters=filters,
    )
    if not responses:
        raise SimpleSyllabusImportError(
            "No saved Kean Simple Syllabus session is available. Click Login and sync, finish Kean login, then try this page again."
        )
    write_library_search_meta_from_responses(responses)
    payload = normalize_scraped_responses(responses)
    counts = load_syllabus_payload(db, payload, reset=reset)
    db.commit()
    result = BrowserSyncResult(
        counts=counts,
        response_count=len(responses),
        course_count=sum(len(term.get("courses", [])) for term in payload.get("terms", [])),
    )
    write_simple_syllabus_auth_status(
        "success",
        f"Loaded Syllabus Library page {page_number + 1} with the saved Kean session.",
        response_count=result.response_count,
        course_count=result.course_count,
    )
    return result


def sync_course_detail_from_saved_session(db: Session, course: Any) -> BrowserSyncResult | None:
    doc_code = getattr(course, "simple_syllabus_doc_code", None)
    if not doc_code:
        return None
    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    list_item = {
        "code": doc_code,
        "title": f"{course.subject} {course.course_number} {course.section}".strip(),
        "subtitle": course.title,
        "term_name": course.term.code if course.term else "",
        "term_id": getattr(course, "term_external_id", "") or "",
        "entity_id": getattr(course, "entity_external_id", "") or "",
        "entity_type": "section",
        "family_name": "syllabus",
        "editors": [{"full_name": course.instructor.full_name if course.instructor else "Kean Instructor"}],
    }
    append_response(
        responses,
        seen,
        "https://kean.simplesyllabus.com/api2/doc-library-search?course_detail=true",
        {"items": [list_item], "pagination": {"total": 1, "returned": 1, "page": 0, "page_size": 1}},
    )
    doc_url = f"https://kean.simplesyllabus.com/api2/doc?{urlencode({'code': doc_code})}"
    doc_payload = fetch_simple_syllabus_json_with_session(doc_url)
    if isinstance(doc_payload, dict):
        append_response(responses, seen, doc_url, doc_payload)
        doc_item = next((item for item in extract_response_items(doc_payload) if isinstance(item, dict)), {})
        term_id = str(doc_item.get("term_id") or getattr(course, "term_external_id", "") or "")
        entity_id = str(doc_item.get("entity_id") or getattr(course, "entity_external_id", "") or "")
    else:
        term_id = str(getattr(course, "term_external_id", "") or "")
        entity_id = str(getattr(course, "entity_external_id", "") or "")
    if term_id and entity_id:
        heading_url = "https://kean.simplesyllabus.com/api2/heading-component?" + urlencode(
            {"term_id": term_id, "family_name": "syllabus", "entity_id": entity_id}
        )
        heading_payload = fetch_simple_syllabus_json_with_session(heading_url)
        if isinstance(heading_payload, dict):
            append_response(responses, seen, heading_url, heading_payload)
    if len(responses) <= 1:
        return None
    payload = normalize_scraped_responses(responses)
    counts = load_syllabus_payload(db, payload, reset=False)
    db.commit()
    return BrowserSyncResult(
        counts=counts,
        response_count=len(responses),
        course_count=sum(len(term.get("courses", [])) for term in payload.get("terms", [])),
    )


def sync_material_counts_from_saved_session(db: Session, course_ids: list[int]) -> dict[str, int]:
    courses = [db.get(Course, course_id) for course_id in course_ids[:80]]
    loaded_courses = [course for course in courses if course is not None]
    counts = {
        str(course.id): max(len(course.materials), int(course.material_count_hint or 0))
        for course in loaded_courses
    }
    url_to_course: dict[str, Course] = {}
    for course in loaded_courses:
        term_id = course.term_external_id
        entity_id = course.entity_external_id
        if not term_id or not entity_id:
            continue
        heading_url = "https://kean.simplesyllabus.com/api2/heading-component?" + urlencode(
            {
                "term_id": str(term_id),
                "family_name": "syllabus",
                "entity_id": str(entity_id),
            }
        )
        url_to_course[heading_url] = course

    for result in fetch_json_batch_with_session(
        list(url_to_course),
        concurrency=16,
        timeout_seconds=8.0,
        update_status=False,
    ):
        url = result.get("url")
        payload = result.get("payload")
        course = url_to_course.get(str(url))
        if not course or not isinstance(payload, dict):
            continue
        materials = dedupe_materials(extract_materials({"items": iter_dicts(payload)}))
        material_count = len(materials)
        course.material_count_hint = material_count
        counts[str(course.id)] = material_count

    db.flush()
    return counts


def sync_from_har(db: Session, raw_content: bytes, *, reset: bool = True) -> BrowserSyncResult:
    responses = extract_json_responses_from_har(raw_content)
    if reset:
        clear_library_search_meta()
    write_library_search_meta_from_responses(responses)
    payload = normalize_scraped_responses(responses)
    counts = load_syllabus_payload(db, payload, reset=reset)
    return BrowserSyncResult(
        counts=counts,
        response_count=len(responses),
        course_count=sum(len(term.get("courses", [])) for term in payload.get("terms", [])),
    )


def capture_simple_syllabus_json(*, timeout_seconds: int = 180) -> list[dict[str, Any]]:
    global _browser_sync_capture
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SimpleSyllabusImportError("Playwright is not installed. Run python -m pip install -r requirements.txt.") from exc

    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    started = time.monotonic()

    _browser_sync_capture = {"responses": responses, "seen": seen}
    context = get_or_launch_browser_context(sync_playwright)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(timeout_seconds * 1_000)
        try:
            page.goto(SIMPLE_SYLLABUS_MY_COURSES_URL, wait_until="domcontentloaded", timeout=60_000)
            try_select_historic_filter(page)
            wait_for_responses(page, responses, started, timeout_seconds, require_details=False)
            page = active_simple_syllabus_page(context) or page
            fetch_full_my_courses_search(page, responses, seen)
            page = active_simple_syllabus_page(context) or page
            page.goto(SIMPLE_SYLLABUS_LIBRARY_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            fetch_library_search_page(page, responses, seen, page_number=0, page_size=50)
        except PlaywrightTimeoutError as exc:
            raise SimpleSyllabusImportError("Timed out while waiting for Kean Simple Syllabus data to load.") from exc
    finally:
        _browser_sync_capture = None

    if not responses:
        raise SimpleSyllabusImportError(
            "No Simple Syllabus JSON responses were captured. Sign in in the opened browser window, then run automatic sync again."
        )
    return responses


def active_simple_syllabus_page(context: Any) -> Any | None:
    for page in reversed(list(context.pages)):
        try:
            if "kean.simplesyllabus.com" in page.url:
                return page
        except Exception:
            continue
    for page in reversed(list(context.pages)):
        try:
            page.url
            return page
        except Exception:
            continue
    return None


def capture_simple_syllabus_library_page(
    *,
    page_number: int,
    page_size: int = 50,
    filters: dict[str, str] | None = None,
    timeout_seconds: int = 60,
) -> list[dict[str, Any]]:
    global _browser_sync_capture
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SimpleSyllabusImportError("Playwright is not installed. Run python -m pip install -r requirements.txt.") from exc

    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    _browser_sync_capture = {"responses": responses, "seen": seen}
    context = get_or_launch_browser_context(sync_playwright)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(timeout_seconds * 1_000)
        try:
            page.goto(SIMPLE_SYLLABUS_LIBRARY_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1_000)
            save_simple_syllabus_session_from_context(context)
            fetch_library_search_page_with_session(
                responses,
                seen,
                page_number=page_number,
                page_size=page_size,
                filters=filters,
            )
            if not responses:
                fetch_library_search_page(
                    page,
                    responses,
                    seen,
                    page_number=page_number,
                    page_size=page_size,
                    filters=filters,
                )
        except PlaywrightTimeoutError as exc:
            raise SimpleSyllabusImportError("Timed out while loading the requested Kean Syllabus Library page.") from exc
    finally:
        _browser_sync_capture = None

    if not responses:
        raise SimpleSyllabusImportError(
            "No Simple Syllabus library page data was captured. Sign in in the opened browser window, then try this page again."
        )
    return responses


def get_or_launch_browser_context(sync_playwright_factory: Any) -> Any:
    global _browser_sync_context
    global _browser_sync_playwright

    if _browser_sync_context is not None:
        try:
            _browser_sync_context.pages
            return _browser_sync_context
        except Exception:
            _browser_sync_context = None

    if _browser_sync_playwright is None:
        _browser_sync_playwright = sync_playwright_factory().start()

    last_error: Exception | None = None
    launch_attempts = (
        {"channel": "msedge", "headless": False},
        {"channel": "chrome", "headless": False},
        {"headless": False},
    )
    for profile_dir in browser_profile_dirs():
        for options in launch_attempts:
            try:
                _browser_sync_context = _browser_sync_playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    **options,
                )
                _browser_sync_context.on("response", capture_browser_response)
                return _browser_sync_context
            except Exception as exc:
                last_error = exc
    raise SimpleSyllabusImportError("Could not open a browser window for automatic sync.") from last_error


def reset_browser_sync_context() -> None:
    global _browser_sync_context
    global _browser_sync_playwright

    try:
        if _browser_sync_context is not None:
            _browser_sync_context.close()
    except Exception:
        pass
    try:
        if _browser_sync_playwright is not None:
            _browser_sync_playwright.stop()
    except Exception:
        pass
    _browser_sync_context = None
    _browser_sync_playwright = None


def forget_browser_sync_context() -> None:
    global _browser_sync_context
    global _browser_sync_playwright

    _browser_sync_context = None
    _browser_sync_playwright = None


def minimize_browser_context(context: Any) -> None:
    for page in reversed(list(getattr(context, "pages", []) or [])):
        try:
            session = context.new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            window_id = window.get("windowId")
            if window_id:
                session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "minimized"}})
                return
        except Exception:
            continue


def capture_browser_response(response: Any) -> None:
    capture = _browser_sync_capture
    if capture is None:
        return
    url = response.url
    if "kean.simplesyllabus.com" not in url or not looks_like_simple_syllabus_endpoint(url):
        return
    key = f"{response.request.method}:{url}"
    seen: set[str] = capture["seen"]
    if key in seen:
        return
    try:
        payload = response.json()
    except Exception:
        return
    append_response(capture["responses"], seen, url, payload, method=response.request.method)


def append_response(
    responses: list[dict[str, Any]],
    seen: set[str],
    url: str,
    payload: Any,
    *,
    method: str = "GET",
) -> bool:
    key = f"{method}:{url}"
    if key in seen:
        return False
    seen.add(key)
    responses.append({"url": url, "payload": payload})
    return True


def browser_profile_dir() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".wkcoursekit-simple-syllabus-browser"


def browser_profile_dirs() -> list[Any]:
    from app.database import BASE_DIR

    primary = browser_profile_dir()
    fallback = BASE_DIR / f".wkcoursekit-simple-syllabus-browser-{os.getpid()}"
    return [primary, fallback] if fallback != primary else [primary]


def wait_for_responses(
    page: Any,
    responses: list[dict[str, Any]],
    started: float,
    timeout_seconds: int,
    *,
    require_details: bool,
) -> None:
    while time.monotonic() - started < timeout_seconds:
        has_list = any("doc-library-search" in item["url"] for item in responses)
        has_details = any("doc?code=" in item["url"] or "/doc/" in item["url"] for item in responses)
        if has_list and (has_details or not require_details):
            page.wait_for_timeout(2_000)
            return
        page.wait_for_timeout(1_000)


def try_interaction_pass(page: Any) -> None:
    try:
        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(800)
        page.mouse.wheel(0, -1600)
        page.wait_for_timeout(800)
    except Exception:
        return


def try_select_historic_filter(page: Any) -> None:
    try:
        selected = page.evaluate(
            """async () => {
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const candidates = Array.from(document.querySelectorAll(
                    '[role="combobox"], mat-select, .mat-mdc-select, .mat-select-trigger, .mat-mdc-form-field'
                )).filter(isVisible);
                const termTrigger = candidates.find((element) => {
                    const text = norm(element.innerText || element.textContent);
                    const label = norm(element.getAttribute('aria-label') || element.getAttribute('aria-labelledby'));
                    return /Term/i.test(text) || /Term/i.test(label) || /Future|Current|Historic|2026|2025|2024|2023/.test(text);
                }) || candidates[0];
                if (termTrigger) {
                    termTrigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    termTrigger.click();
                    await sleep(600);
                }

                const options = Array.from(document.querySelectorAll(
                    'mat-option, .mat-mdc-option, [role="option"], mat-checkbox, .mat-mdc-checkbox, label, .mdc-form-field'
                )).filter(isVisible).filter((element) => /^Historic$/i.test(norm(element.innerText || element.textContent)));
                const option = options[0];
                if (!option) return false;
                const input = option.querySelector('input[type="checkbox"]')
                    || option.closest('mat-option, .mat-mdc-option, mat-checkbox, .mat-mdc-checkbox, .mdc-form-field')?.querySelector('input[type="checkbox"]');
                const selected = Boolean(
                    input?.checked
                    || input?.classList.contains('mdc-checkbox--selected')
                    || option.getAttribute('aria-selected') === 'true'
                    || option.getAttribute('aria-checked') === 'true'
                    || option.classList.contains('mat-mdc-option-active')
                    || option.classList.contains('mdc-list-item--selected')
                    || option.querySelector('.mdc-checkbox--selected, .mat-mdc-checkbox-checked')
                );
                if (!selected) {
                    const target = input || option;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.click();
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    await sleep(800);
                }
                return true;
            }"""
        )
        if not selected:
            page.get_by_text("Historic", exact=True).click(timeout=2_000)
        page.wait_for_timeout(1_000)
    except Exception:
        return


def fetch_full_my_courses_search(page: Any, responses: list[dict[str, Any]], seen: set[str]) -> None:
    search_url = build_historic_my_courses_search_url(responses)
    if not search_url:
        return
    payload = fetch_json_with_page(page, search_url)
    if isinstance(payload, dict):
        append_response(responses, seen, search_url, payload)


def fetch_library_search_page(
    page: Any,
    responses: list[dict[str, Any]],
    seen: set[str],
    *,
    page_number: int,
    page_size: int | None = None,
    filters: dict[str, str] | None = None,
) -> None:
    parsed = urlparse(SIMPLE_SYLLABUS_LIBRARY_URL)
    search_path = "/api2/doc-library-search"
    query: dict[str, Any] = dict(filters or {})
    query["page"] = page_number
    if page_size:
        query["page_size"] = page_size
    search_url = urlunparse((parsed.scheme, parsed.netloc, search_path, "", urlencode(query), ""))
    payload = fetch_json_with_page(page, search_url)
    if not isinstance(payload, dict):
        return

    append_response(responses, seen, search_url, payload)
    write_library_search_meta(payload, filters=filters)
    write_library_page_cache(payload, filters=filters, page_number=page_number, source_url=search_url)
    if not normalize_library_filters(filters) and page_number == 0:
        fetch_library_facets_with_page(page, responses, seen)


def fetch_library_search_page_with_session(
    responses: list[dict[str, Any]],
    seen: set[str],
    *,
    page_number: int,
    page_size: int | None = None,
    filters: dict[str, str] | None = None,
) -> None:
    parsed = urlparse(SIMPLE_SYLLABUS_LIBRARY_URL)
    search_path = "/api2/doc-library-search"
    query: dict[str, Any] = dict(filters or {})
    query["page"] = page_number
    if page_size:
        query["page_size"] = page_size
    search_url = urlunparse((parsed.scheme, parsed.netloc, search_path, "", urlencode(query), ""))
    payload = fetch_simple_syllabus_json_with_session(search_url)
    if not isinstance(payload, dict):
        return
    append_response(responses, seen, search_url, payload)
    fetch_session_account_with_session(responses, seen)
    write_library_search_meta(payload, filters=filters)
    write_library_page_cache(payload, filters=filters, page_number=page_number, source_url=search_url)
    if not normalize_library_filters(filters) and page_number == 0:
        fetch_library_facets_with_session(responses, seen)


def fetch_session_account_with_session(responses: list[dict[str, Any]], seen: set[str]) -> None:
    if read_simple_syllabus_session().get("account_name"):
        return
    parsed = urlparse(SIMPLE_SYLLABUS_LIBRARY_URL)
    session_url = urlunparse((parsed.scheme, parsed.netloc, "/api/session", "", "", ""))
    payload = fetch_simple_syllabus_json_with_session(session_url)
    if isinstance(payload, dict):
        append_response(responses, seen, session_url, payload)
        save_simple_syllabus_account_from_responses(responses)


def fetch_library_facets_with_page(page: Any, responses: list[dict[str, Any]], seen: set[str]) -> None:
    facets = read_library_filter_facets()
    if facets_have_term_metadata(facets) and facets.get("subjects"):
        return
    for facet_url in library_facet_urls():
        payload = fetch_json_with_page(page, facet_url)
        if isinstance(payload, dict):
            append_response(responses, seen, facet_url, payload)
    write_library_facets_from_responses(responses)


def fetch_library_facets_with_session(responses: list[dict[str, Any]], seen: set[str]) -> None:
    facets = read_library_filter_facets()
    if facets_have_term_metadata(facets) and facets.get("subjects"):
        return
    for facet_url in library_facet_urls():
        payload = fetch_simple_syllabus_json_with_session(facet_url)
        if isinstance(payload, dict):
            append_response(responses, seen, facet_url, payload)
    write_library_facets_from_responses(responses)


def library_facet_urls() -> list[str]:
    parsed = urlparse(SIMPLE_SYLLABUS_LIBRARY_URL)
    return [
        urlunparse((parsed.scheme, parsed.netloc, "/api2/app-state", "", urlencode({"locale": "en-US"}), "")),
        urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                "/api2/subject",
                "",
                urlencode({"is_active": "true", "bypass_pagination": "true", "request_page": "syllabus_library"}),
                "",
            )
        ),
    ]


def fetch_simple_syllabus_json_with_session(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    update_status: bool = True,
) -> Any:
    session = read_simple_syllabus_session()
    cookie = session.get("cookie")
    token = session.get("bearer_token")
    if not cookie and not token:
        return None
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Referer": SIMPLE_SYLLABUS_LIBRARY_URL,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-App-Version-Client": simple_syllabus_app_version(),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
        ),
    }
    if cookie:
        headers["Cookie"] = str(cookie)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    try:
        try:
            client_context = httpx.Client(headers=headers, follow_redirects=True, timeout=timeout_seconds, http2=True)
        except TypeError:
            client_context = httpx.Client(headers=headers, follow_redirects=True, timeout=timeout_seconds)
        with client_context as client:
            response = client.get(url)
            if response.status_code in {401, 403}:
                if update_status:
                    write_simple_syllabus_auth_status(
                        "error",
                        "Saved Kean Simple Syllabus session is no longer authorized. Click Login and sync again.",
                    )
                return None
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        if update_status:
            write_simple_syllabus_auth_status(
                "error",
                f"Could not fetch Kean Simple Syllabus API with the saved session. Refresh Kean login, then retry. {type(exc).__name__}: {exc}",
            )
        return None


def fetch_json_batch_with_session(
    urls: list[str],
    *,
    concurrency: int = 8,
    timeout_seconds: float = 30.0,
    update_status: bool = True,
) -> list[dict[str, Any]]:
    if not urls:
        return []
    output: list[dict[str, Any]] = []
    workers = max(1, min(concurrency, len(urls)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_simple_syllabus_json_with_session,
                url,
                timeout_seconds=timeout_seconds,
                update_status=update_status,
            ): url
            for url in urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                payload = future.result()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                output.append({"url": url, "payload": payload})
    return output


def simple_syllabus_app_version() -> str:
    global _simple_syllabus_app_version
    if _simple_syllabus_app_version:
        return _simple_syllabus_app_version
    fallback = "2026.05.08.11.07"
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            response = client.get(SIMPLE_SYLLABUS_LIBRARY_URL)
            response.raise_for_status()
            script_match = re.search(r'src="([^"]*main\.[^"]+\.js)"', response.text)
            if not script_match:
                _simple_syllabus_app_version = fallback
                return _simple_syllabus_app_version
            script_url = urljoin(str(response.url), script_match.group(1))
            script_response = client.get(script_url)
            script_response.raise_for_status()
            version_match = re.search(r'"(20\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{2})"', script_response.text)
            _simple_syllabus_app_version = version_match.group(1) if version_match else fallback
    except Exception:
        _simple_syllabus_app_version = fallback
    return _simple_syllabus_app_version


def fetch_all_library_search(page: Any, responses: list[dict[str, Any]], seen: set[str]) -> None:
    fetch_library_search_page(page, responses, seen, page_number=0)


def write_library_search_meta(payload: dict[str, Any], *, filters: dict[str, str] | None = None) -> None:
    total = extract_total(payload)
    page_size = extract_page_size(payload) or len(extract_response_items(payload))
    if total is None or not page_size:
        return
    key = library_search_meta_key(filters)
    document = read_library_search_meta_document()
    document[key] = {"total": total, "page_size": page_size, "filters": normalize_library_filters(filters)}
    try:
        library_search_meta_path().write_text(
            json.dumps(document, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def write_library_search_meta_from_responses(responses: list[dict[str, Any]]) -> None:
    write_library_facets_from_responses(responses)
    for response in responses:
        url = response.get("url", "")
        if "doc-library-search" not in url or is_my_courses_search_url(url):
            continue
        payload = response.get("payload")
        if isinstance(payload, dict) and extract_total(payload):
            write_library_search_meta(payload, filters=library_filters_from_url(url))
            write_library_page_cache(
                payload,
                filters=library_filters_from_url(url),
                page_number=extract_page_number(payload),
                source_url=url,
            )
            return


def write_library_facets_from_responses(responses: list[dict[str, Any]]) -> None:
    existing = read_library_filter_facets()
    terms = list(existing.get("terms", []))
    subjects = list(existing.get("subjects", []))
    for response in responses:
        url = response.get("url", "")
        payload = response.get("payload")
        if not isinstance(payload, dict):
            continue
        if "app-state" in url:
            terms = merge_term_facets(terms, extract_term_facets(payload))
        elif "/subject" in url:
            subjects = sorted(set(subjects) | set(extract_subject_facets(payload)))
    document = {"terms": terms, "subjects": subjects}
    if not terms and not subjects:
        return
    try:
        library_facets_path().write_text(
            json.dumps(document, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def read_library_filter_facets() -> dict[str, Any]:
    try:
        payload = json.loads(library_facets_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"terms": [], "subjects": []}
    if not isinstance(payload, dict):
        return {"terms": [], "subjects": []}
    terms = [
        {
            "code": str(term.get("code")),
            "name": str(term.get("name") or term.get("code")),
            "entity_id": str(term.get("entity_id") or ""),
            "status": str(term.get("status") or ""),
            "start_date": str(term.get("start_date") or ""),
            "end_date": str(term.get("end_date") or ""),
        }
        for term in payload.get("terms", [])
        if isinstance(term, dict) and term.get("code")
    ]
    subjects = sorted({str(subject).strip().upper() for subject in payload.get("subjects", []) if str(subject).strip()})
    return {"terms": terms, "subjects": subjects}


def facets_have_term_ids(facets: dict[str, Any]) -> bool:
    terms = facets.get("terms", [])
    return bool(terms) and any(isinstance(term, dict) and term.get("entity_id") for term in terms)


def facets_have_term_metadata(facets: dict[str, Any]) -> bool:
    terms = facets.get("terms", [])
    return bool(terms) and any(
        isinstance(term, dict) and term.get("entity_id") and term.get("start_date")
        for term in terms
    )


def extract_term_facets(payload: dict[str, Any]) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    for item in extract_response_items(payload):
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        for term in state.get("terms", []) if isinstance(state.get("terms"), list) else []:
            if not isinstance(term, dict) or term.get("is_active") is False:
                continue
            code = str(term.get("name") or term.get("code") or "").strip()
            if not code:
                continue
            terms.append(
                {
                    "code": code,
                    "name": code,
                    "entity_id": str(term.get("entity_id") or ""),
                    "status": str(term.get("status") or ""),
                    "start_date": str(term.get("start_date") or ""),
                    "end_date": str(term.get("end_date") or ""),
                }
            )
    return merge_term_facets([], terms)


def extract_subject_facets(payload: dict[str, Any]) -> list[str]:
    subjects: set[str] = set()
    for item in extract_response_items(payload):
        if item.get("is_active") is False:
            continue
        name = str(item.get("name") or item.get("code") or "").strip().upper()
        if name:
            subjects.add(name)
    return sorted(subjects)


def merge_term_facets(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    by_code: dict[str, dict[str, str]] = {}
    for term in [*existing, *incoming]:
        code = str(term.get("code", "")).strip()
        if not code:
            continue
        current = by_code.get(code, {})
        by_code[code] = {
            "code": code,
            "name": str(term.get("name") or current.get("name") or code),
            "entity_id": str(term.get("entity_id") or current.get("entity_id") or ""),
            "status": str(term.get("status") or current.get("status") or ""),
            "start_date": str(term.get("start_date") or current.get("start_date") or ""),
            "end_date": str(term.get("end_date") or current.get("end_date") or ""),
        }
    return sorted(by_code.values(), key=lambda term: term_facet_sort_key(term["code"]), reverse=True)


def term_facet_sort_key(code: str) -> tuple[int, int, str]:
    text = code.upper().replace("/", "")
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


def read_library_search_meta(filters: dict[str, str] | None = None) -> dict[str, int]:
    payload = read_library_search_meta_document().get(library_search_meta_key(filters), {})
    meta: dict[str, int] = {}
    if not isinstance(payload, dict):
        return meta
    for key in ("total", "page_size"):
        try:
            value = int(payload.get(key, 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            meta[key] = value
    return meta


def read_library_search_meta_document() -> dict[str, Any]:
    try:
        payload = json.loads(library_search_meta_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if "total" in payload:
        return {library_search_meta_key(None): payload}
    return payload if isinstance(payload, dict) else {}


def clear_library_search_meta() -> None:
    try:
        library_search_meta_path().unlink(missing_ok=True)
    except OSError:
        pass
    try:
        library_page_cache_path().unlink(missing_ok=True)
    except OSError:
        pass
    try:
        library_facets_path().unlink(missing_ok=True)
    except OSError:
        pass


def library_search_meta_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".simple_syllabus_library_meta.json"


def library_page_cache_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".simple_syllabus_library_pages.json"


def library_facets_path() -> Any:
    from app.database import BASE_DIR

    return BASE_DIR / ".simple_syllabus_library_facets.json"


def library_search_meta_key(filters: dict[str, str] | None = None) -> str:
    normalized = normalize_library_filters(filters)
    if not normalized:
        return "__all__"
    return json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def write_library_page_cache(
    payload: dict[str, Any],
    *,
    filters: dict[str, str] | None,
    page_number: int,
    source_url: str,
) -> None:
    keys: list[dict[str, str]] = []
    for item in extract_response_items(payload):
        course = course_from_item(item, source_url=source_url)
        if not course:
            continue
        keys.append(
            {
                "term": course["_term_code"],
                "subject": course["subject"],
                "course_number": course["course_number"],
                "section": course.get("section", ""),
            }
        )
    if not keys:
        return
    try:
        document = json.loads(library_page_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    cache_key = library_search_meta_key(filters)
    pages = document.setdefault(cache_key, {})
    if not isinstance(pages, dict):
        pages = {}
        document[cache_key] = pages
    pages[str(page_number)] = keys
    try:
        library_page_cache_path().write_text(
            json.dumps(document, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def read_library_page_cache(
    *,
    filters: dict[str, str] | None = None,
    page_number: int,
) -> list[dict[str, str]]:
    try:
        document = json.loads(library_page_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(document, dict):
        return []
    pages = document.get(library_search_meta_key(filters), {})
    if not isinstance(pages, dict):
        return []
    keys = pages.get(str(page_number), [])
    return [key for key in keys if isinstance(key, dict)]


def normalize_library_filters(filters: dict[str, str] | None = None) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in sorted((filters or {}).items())
        if value is not None and str(value) != ""
    }


def library_filters_from_url(url: str) -> dict[str, str]:
    query = parse_qs(urlparse(url).query)
    filters: dict[str, str] = {}
    for key, values in query.items():
        if key in {"page", "page_size"}:
            continue
        if values:
            filters[key] = values[0]
    return filters


def build_library_search_filters(
    *,
    q: str | None = None,
    course_code: str | None = None,
    title: str | None = None,
    instructor: str | None = None,
    term: str | None = None,
    subject: str | None = None,
) -> dict[str, str]:
    filters: dict[str, str] = {}
    search_parts = [part.strip() for part in (q, title) if part and part.strip()]
    if search_parts:
        filters["search"] = " ".join(search_parts)
    code_subject, code_number = parse_course_code(course_code or "")
    if subject and subject.strip():
        filters["subject_name"] = subject.strip().upper()
    elif code_subject:
        filters["subject_name"] = code_subject
    if code_number:
        filters["course_number"] = code_number
    if instructor and instructor.strip():
        filters["editor"] = instructor.strip()
    if term and term.strip():
        term_id = library_term_entity_id(term.strip())
        if term_id:
            filters["term_ids[]"] = term_id
        else:
            filters["search"] = " ".join([filters.get("search", ""), term.strip()]).strip()
    return filters


def library_term_entity_id(term_code: str) -> str:
    for term in read_library_filter_facets().get("terms", []):
        if not isinstance(term, dict):
            continue
        if str(term.get("code", "")).strip().upper() == term_code.strip().upper():
            return str(term.get("entity_id") or "")
    return ""


def extract_response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items", []) or payload.get("results", [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def extract_total(payload: dict[str, Any]) -> int | None:
    for container_key in ("pagination", "meta"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            nested = extract_total(container)
            if nested is not None:
                return nested
    for key in ("total", "total_count", "totalCount", "count"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def extract_page_size(payload: dict[str, Any]) -> int | None:
    for container_key in ("pagination", "meta"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            nested = extract_page_size(container)
            if nested is not None:
                return nested
    for key in ("page_size", "pageSize", "per_page", "perPage", "limit"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            page_size = int(value)
        except (TypeError, ValueError):
            continue
        if page_size > 0:
            return page_size
    return None


def extract_page_number(payload: dict[str, Any]) -> int:
    for container_key in ("pagination", "meta"):
        container = payload.get(container_key)
        if isinstance(container, dict):
            nested = extract_page_number(container)
            if nested or any(key in container for key in ("page", "page_number", "pageNumber")):
                return nested
    for key in ("page", "page_number", "pageNumber"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def build_historic_my_courses_search_url(responses: list[dict[str, Any]]) -> str | None:
    for response in responses:
        url = response.get("url", "")
        if "doc-library-search" not in url or "my_courses_account_id" not in url:
            continue
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        account_id = query.get("my_courses_account_id", [None])[0]
        if not account_id:
            continue
        full_query = {
            "my_courses_account_id": account_id,
            "term_statuses[]": ["future", "current", "historic"],
        }
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(full_query, doseq=True), ""))
    return None


def is_my_courses_search_url(url: str) -> bool:
    if "/my-courses" in url:
        return True
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "my_courses_account_id" in query:
        return True
    return any(value.lower() == "true" for value in query.get("my_courses", []))


def fetch_api_details_for_courses(
    page: Any,
    responses: list[dict[str, Any]],
    seen: set[str],
    *,
    my_courses_only: bool = False,
) -> None:
    urls: list[str] = []
    for item in course_list_items(responses, my_courses_only=my_courses_only):
        doc_code = extract_doc_code("", item)
        term_id = item.get("term_id")
        entity_id = extract_entity_id("", item)
        if doc_code:
            doc_url = f"https://kean.simplesyllabus.com/api2/doc?{urlencode({'code': doc_code})}"
            if f"GET:{doc_url}" not in seen:
                urls.append(doc_url)
        if term_id and entity_id:
            heading_url = "https://kean.simplesyllabus.com/api2/heading-component?" + urlencode(
                {
                    "term_id": str(term_id),
                    "family_name": "syllabus",
                    "entity_id": str(entity_id),
                }
            )
            if f"GET:{heading_url}" not in seen:
                urls.append(heading_url)
    for result in fetch_json_batch_with_page(page, urls):
        url = result.get("url")
        payload = result.get("payload")
        if isinstance(url, str) and isinstance(payload, dict):
            append_response(responses, seen, url, payload)


def fetch_materials_for_course_list_with_page(
    page: Any,
    responses: list[dict[str, Any]],
    seen: set[str],
    *,
    my_courses_only: bool = False,
) -> None:
    urls = heading_component_urls_for_course_list(responses, seen, my_courses_only=my_courses_only)
    for result in fetch_json_batch_with_page(page, urls, concurrency=8):
        url = result.get("url")
        payload = result.get("payload")
        if isinstance(url, str) and isinstance(payload, dict):
            if append_response(responses, seen, url, payload):
                responses[-1]["material_count_only"] = True


def fetch_materials_for_course_list_with_session(responses: list[dict[str, Any]], seen: set[str]) -> None:
    urls = heading_component_urls_for_course_list(responses, seen)
    for result in fetch_json_batch_with_session(urls, concurrency=10, timeout_seconds=10.0, update_status=False):
        url = result.get("url")
        payload = result.get("payload")
        if isinstance(url, str) and isinstance(payload, dict):
            if append_response(responses, seen, url, payload):
                responses[-1]["material_count_only"] = True


def heading_component_urls_for_course_list(
    responses: list[dict[str, Any]],
    seen: set[str],
    *,
    my_courses_only: bool = False,
) -> list[str]:
    urls: list[str] = []
    for item in course_list_items(responses, my_courses_only=my_courses_only):
        term_id = item.get("term_id")
        entity_id = extract_entity_id("", item)
        if not term_id or not entity_id:
            continue
        heading_url = "https://kean.simplesyllabus.com/api2/heading-component?" + urlencode(
            {
                "term_id": str(term_id),
                "family_name": "syllabus",
                "entity_id": str(entity_id),
            }
        )
        if f"GET:{heading_url}" not in seen:
            urls.append(heading_url)
    return urls


def course_list_items(responses: list[dict[str, Any]], *, my_courses_only: bool = False) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for response in responses:
        url = response.get("url", "")
        if "doc-library-search" not in url:
            continue
        if my_courses_only and not is_my_courses_search_url(url):
            continue
        payload = response.get("payload")
        if not isinstance(payload, dict):
            continue
        for item in extract_response_items(payload):
            key = extract_doc_code("", item) or extract_entity_id("", item) or json.dumps(item, default=str)
            items[str(key)] = item
    return list(items.values())


def fetch_json_with_page(page: Any, url: str) -> Any:
    try:
        return page.evaluate(
            """async ({ url, appVersion }) => {
                const headers = { 'Accept': 'application/json' };
                if (appVersion) headers['x-app-version-client'] = appVersion;
                const response = await fetch(url, { credentials: 'include', headers });
                if (!response.ok) return null;
                return await response.json();
            }""",
            {"url": url, "appVersion": simple_syllabus_app_version()},
        )
    except Exception:
        try:
            return page.evaluate(
                """async (url) => {
                    const response = await fetch(url, { credentials: 'include', headers: { 'Accept': 'application/json' } });
                    if (!response.ok) return null;
                    return await response.json();
                }""",
                url,
            )
        except Exception:
            return None


def fetch_json_batch_with_page(page: Any, urls: list[str], *, concurrency: int = 6) -> list[dict[str, Any]]:
    if not urls:
        return []
    try:
        results = page.evaluate(
            """async ({ urls, concurrency, appVersion }) => {
                const output = [];
                let index = 0;
                async function worker() {
                    while (index < urls.length) {
                        const url = urls[index++];
                        try {
                            const controller = new AbortController();
                            const timer = setTimeout(() => controller.abort(), 30000);
                            const headers = { 'Accept': 'application/json' };
                            if (appVersion) headers['x-app-version-client'] = appVersion;
                            const response = await fetch(url, {
                                credentials: 'include',
                                headers,
                                signal: controller.signal
                            });
                            clearTimeout(timer);
                            if (response.ok) output.push({ url, payload: await response.json() });
                        } catch (error) {}
                    }
                }
                await Promise.all(Array.from({ length: Math.min(concurrency, urls.length) }, worker));
                return output;
            }""",
            {"urls": urls, "concurrency": concurrency, "appVersion": simple_syllabus_app_version()},
        )
    except Exception:
        return []
    return results if isinstance(results, list) else []


def fetch_missing_doc_details(page: Any, responses: list[dict[str, Any]]) -> None:
    doc_records = collect_doc_records(responses)
    existing = {
        extract_doc_code(response.get("url", ""))
        for response in responses
        if "doc?code=" in response.get("url", "") or "/doc/" in response.get("url", "")
    }
    for doc_code, doc_slug in sorted(doc_records.items()):
        if doc_code in existing:
            continue
        detail_url = build_doc_view_url(doc_code, doc_slug)
        api_url = build_doc_detail_url(responses, doc_code)
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        try:
            dom_payload = scrape_doc_page_dom(page, detail_url, doc_code)
        except Exception:
            dom_payload = None
        if isinstance(dom_payload, dict) and (dom_payload.get("sections") or dom_payload.get("materials") or dom_payload.get("instructor")):
            responses.append({"url": detail_url, "payload": {"_dom_detail": dom_payload}})
        if not api_url:
            continue
        payload = fetch_json_with_page(page, api_url)
        if isinstance(payload, dict):
            responses.append({"url": api_url, "payload": payload})


def collect_doc_records(responses: list[dict[str, Any]]) -> dict[str, str]:
    records: dict[str, str] = {}
    for response in responses:
        source_url = response.get("url", "")
        url_code = extract_doc_code(source_url)
        url_slug = extract_doc_slug(source_url)
        if url_code and url_slug:
            records[url_code] = url_slug
        for item in iter_dicts(response.get("payload")):
            doc_code = extract_doc_code(source_url, item)
            if not doc_code:
                continue
            slug = extract_doc_slug(source_url, item)
            if not slug:
                course = course_from_item(item, source_url=source_url)
                slug = build_course_slug(course) if course else None
            if slug:
                records[doc_code] = slug
    return records


def scrape_doc_page_dom(page: Any, detail_url: str, doc_code: str) -> dict[str, Any]:
    return page.evaluate(
        """({ detailUrl, docCode }) => {
            const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const text = clean(document.body ? document.body.innerText : '');
            const headingNodes = Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]'));
            const sections = [];
            for (const node of headingNodes) {
                const heading = clean(node.innerText || node.textContent);
                if (!heading) continue;
                const parts = [];
                let cursor = node.nextElementSibling;
                while (cursor && !/^H[1-4]$/.test(cursor.tagName) && parts.join(' ').length < 2500) {
                    const value = clean(cursor.innerText || cursor.textContent);
                    if (value) parts.push(value);
                    cursor = cursor.nextElementSibling;
                }
                const body = clean(parts.join(' '));
                if (body) sections.push({ heading, body });
            }
            const rows = Array.from(document.querySelectorAll('table tr')).map((row) => (
                Array.from(row.querySelectorAll('th,td')).map((cell) => clean(cell.innerText || cell.textContent)).filter(Boolean)
            )).filter((cells) => cells.length);
            return {
                docCode,
                docUrl: detailUrl,
                title: clean(document.querySelector('h1')?.innerText || document.title),
                visibleText: text,
                sections,
                tableRows: rows,
            };
        }""",
        {"detailUrl": detail_url, "docCode": doc_code},
    )


def build_doc_view_url(doc_code: str, doc_slug: str) -> str:
    return f"https://kean.simplesyllabus.com/en-US/doc/{doc_code}/{doc_slug}?mode=view"


def build_doc_detail_url(responses: list[dict[str, Any]], doc_code: str) -> str | None:
    for response in responses:
        url = response.get("url", "")
        if "doc-library-search" not in url:
            continue
        parsed = urlparse(url)
        detail_path = parsed.path.replace("doc-library-search", "doc")
        query = urlencode({"code": doc_code})
        return urlunparse((parsed.scheme, parsed.netloc, detail_path, "", query, ""))
    return f"https://kean.simplesyllabus.com/en-US/api/doc?{urlencode({'code': doc_code})}"


def looks_like_simple_syllabus_endpoint(url: str) -> bool:
    endpoint_markers = (
        "doc-library-search",
        "doc?code=",
        "/doc/",
        "heading-component",
        "course-number",
        "subject",
        "session",
        "app-state",
    )
    return any(marker in url for marker in endpoint_markers)


def extract_json_responses_from_har(raw_content: bytes) -> list[dict[str, Any]]:
    try:
        document = json.loads(raw_content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SimpleSyllabusImportError("The uploaded file is not a valid HAR/JSON file.") from exc

    responses: list[dict[str, Any]] = []
    if isinstance(document, dict) and "log" in document:
        entries = document.get("log", {}).get("entries", [])
        for entry in entries:
            url = entry.get("request", {}).get("url", "")
            if "kean.simplesyllabus.com" not in url or not looks_like_simple_syllabus_endpoint(url):
                continue
            content = entry.get("response", {}).get("content", {})
            text = content.get("text")
            if not text:
                continue
            if content.get("encoding") == "base64":
                try:
                    text = base64.b64decode(text).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            responses.append({"url": url, "payload": payload})
    elif isinstance(document, list):
        responses = [
            {"url": item.get("url", ""), "payload": item.get("payload", item)}
            for item in document
            if isinstance(item, dict)
        ]
    elif isinstance(document, dict):
        responses = [{"url": "uploaded-json", "payload": document}]

    if not responses:
        raise SimpleSyllabusImportError(
            "No Simple Syllabus JSON responses were found. In DevTools, enable Preserve log and Save all as HAR with content."
        )
    return responses


def normalize_scraped_responses(responses: list[dict[str, Any]]) -> dict[str, Any]:
    courses: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    materials_by_course: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    doc_code_to_course_key: dict[str, tuple[str, str, str, str]] = {}
    entity_id_to_course_key: dict[str, tuple[str, str, str, str]] = {}
    detail_items_by_doc_code: dict[str, list[dict[str, Any]]] = {}
    detail_items_by_entity_id: dict[str, list[dict[str, Any]]] = {}

    for response in responses:
        doc_code = extract_doc_code(response.get("url", ""))
        if doc_code and not response.get("material_count_only"):
            detail_items_by_doc_code.setdefault(doc_code, []).extend(iter_dicts(response.get("payload")))
        entity_id = extract_entity_id(response.get("url", ""))
        if entity_id and not response.get("material_count_only"):
            detail_items_by_entity_id.setdefault(entity_id, []).extend(iter_dicts(response.get("payload")))

    for response in responses:
        payload = response.get("payload")
        if "heading-component" in response.get("url", ""):
            continue
        for item in iter_dicts(payload):
            course = course_from_item(item, source_url=response.get("url", ""))
            if course is None:
                continue
            key = (
                course["_term_code"],
                course["subject"],
                course["course_number"],
                course.get("section", ""),
            )
            existing = courses.get(key)
            source_size = len(json.dumps(item, default=str))
            if existing is None or source_size > int(existing.get("_source_size", 0)):
                course["enrolled"] = bool(course.get("enrolled") or (existing and existing.get("enrolled")))
                if existing and is_weak_title(course["title"], course["subject"], course["course_number"], course.get("section", "")):
                    course["title"] = existing["title"]
                course["_source_size"] = source_size
                courses[key] = course
            elif course.get("enrolled"):
                courses[key]["enrolled"] = True
            doc_code = extract_doc_code(response.get("url", ""), item)
            if doc_code:
                courses[key]["_doc_code"] = doc_code
                doc_slug = extract_doc_slug(response.get("url", ""), item) or build_course_slug(courses[key])
                if doc_slug:
                    courses[key]["_doc_slug"] = doc_slug
                doc_code_to_course_key[doc_code] = key
            entity_id = extract_entity_id(response.get("url", ""), item)
            if entity_id:
                entity_id_to_course_key[entity_id] = key
            materials = extract_materials(item)
            if materials:
                materials_by_course.setdefault(key, []).extend(materials)

    for response in responses:
        if not response.get("material_count_only"):
            continue
        entity_id = extract_entity_id(response.get("url", ""))
        course_key = entity_id_to_course_key.get(entity_id or "")
        if course_key is None:
            continue
        materials = extract_materials({"items": iter_dicts(response.get("payload"))})
        if materials:
            materials_by_course.setdefault(course_key, []).extend(materials)

    for doc_code, detail_items in detail_items_by_doc_code.items():
        course_key = doc_code_to_course_key.get(doc_code)
        if course_key is None or course_key not in courses:
            continue
        enrich_course_from_detail_items(courses[course_key], detail_items, source_url=f"doc?code={doc_code}")
        for item in detail_items:
            materials = extract_materials(item)
            if materials:
                materials_by_course.setdefault(course_key, []).extend(materials)

    for entity_id, detail_items in detail_items_by_entity_id.items():
        course_key = entity_id_to_course_key.get(entity_id)
        if course_key is None or course_key not in courses:
            continue
        enrich_course_from_detail_items(courses[course_key], detail_items, source_url=f"heading-component?entity_id={entity_id}")
        materials = extract_materials({"items": detail_items})
        if materials:
            materials_by_course.setdefault(course_key, []).extend(materials)

    if not courses:
        raise SimpleSyllabusImportError(
            "Captured Simple Syllabus responses did not contain recognizable course records."
        )

    terms: dict[str, dict[str, Any]] = {}
    for key, course in courses.items():
        term_code = course.pop("_term_code")
        term_name = course.pop("_term_name")
        course.pop("_source_size", None)
        course.pop("_doc_code", None)
        course.pop("_doc_slug", None)
        course["materials"] = dedupe_materials(materials_by_course.get(key, []))
        course["material_count_hint"] = max(int(course.get("material_count_hint") or 0), len(course["materials"]))
        terms.setdefault(
            term_code,
            {
                "code": term_code,
                "name": term_name,
                "courses": [],
            },
        )["courses"].append(course)

    return {
        "student_key": "demo-student",
        "terms": list(terms.values()),
    }


def enrich_course_from_detail_items(course: dict[str, Any], items: list[dict[str, Any]], *, source_url: str) -> None:
    detail_blob = {"items": items}
    instructor = instructor_from_item(detail_blob)
    if instructor["full_name"] != "Kean Instructor":
        course["instructor"] = merge_instructor(course.get("instructor", {}), instructor)

    summary = best_text_value(
        items,
        "summary",
        "description",
        "courseDescription",
        "catalogDescription",
        "overview",
        "learningOutcome",
        "learningOutcomes",
    )
    if summary:
        course["syllabus"]["summary"] = normalize_body(str(summary))

    updated_at = as_iso_datetime(best_text_value(items, "updated_at", "updatedAt", "modifiedAt", "lastModified", "updated"))
    if updated_at:
        course["syllabus"]["updated_at"] = updated_at

    sections = sections_from_item(detail_blob, source_url=source_url)
    component_sections = sections_from_heading_components({"items": items})
    if component_sections:
        sections = component_sections
    if sections:
        incoming_sections = filter_detail_sections(sections)
        existing_sections = course["syllabus"].get("sections", [])
        course["syllabus"]["sections"] = (
            merge_named_sections(existing_sections, incoming_sections) if existing_sections else incoming_sections
        )

    component_instructor = instructor_from_heading_components({"items": items})
    if component_instructor["full_name"] != "Kean Instructor":
        course["instructor"] = merge_instructor(course.get("instructor", {}), component_instructor)
        refresh_instructor_section(course)

    metadata_sections = sections_from_doc_metadata(course, items)
    if metadata_sections:
        current = course["syllabus"].get("sections", [])
        course["syllabus"]["sections"] = merge_named_sections(metadata_sections, current)

    dom_detail = first_dom_detail(items)
    if not dom_detail:
        return
    dom_instructor = instructor_from_dom_detail(dom_detail)
    if dom_instructor["full_name"] != "Kean Instructor":
        course["instructor"] = merge_instructor(course.get("instructor", {}), dom_instructor)
    dom_sections = sections_from_dom_detail(dom_detail)
    if dom_sections:
        course["syllabus"]["sections"] = filter_detail_sections(dom_sections)
        course["syllabus"]["summary"] = dom_sections[0]["body"][:600]
    if dom_detail.get("docUrl"):
        course["syllabus"]["source_label"] = "Kean Simple Syllabus"


def sections_from_doc_metadata(course: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doc_item = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("properties"), dict)
            and ("ca_3" in item["properties"] or "subject_name" in item["properties"])
        ),
        None,
    )
    if not doc_item:
        return []
    properties = doc_item.get("properties") or {}
    term = doc_item.get("term") if isinstance(doc_item.get("term"), dict) else {}
    course_code = f"{course.get('subject', '')} {course.get('course_number', '')} {course.get('section', '')}".strip()
    class_lines = [
        f"Course Title: {properties.get('ca_19') or course.get('title', '')}",
        f"Course Number and Section: {course_code}",
        f"Campus Location: {properties.get('ca_5') or course.get('campus', '')}",
        f"Semester: {term.get('name') or course.get('_term_code', '')}",
    ]
    if properties.get("ca_7"):
        class_lines.append(f"Class Meeting Days and Times: {properties['ca_7']}")
    if properties.get("ca_8"):
        class_lines.append(f"Class Meeting Location: {properties['ca_8']}")
    instructor = course.get("instructor", {})
    instructor_lines = []
    if instructor.get("full_name") and instructor.get("full_name") != "Kean Instructor":
        instructor_lines.append(f"Instructor Name: {instructor['full_name']}")
    if instructor.get("office"):
        instructor_lines.append(f"Office Location: {instructor['office']}")
    if instructor.get("email"):
        instructor_lines.append(f"Email: {instructor['email']}")
    sections = [
        {"heading": "Class Information", "body": "\n".join(class_lines), "sort_order": 10},
    ]
    if instructor_lines:
        sections.append({"heading": "Instructor Information", "body": "\n".join(instructor_lines), "sort_order": 20})
    if properties.get("ca_3"):
        sections.append(
            {
                "heading": "Course Description from Catalog",
                "body": normalize_body(str(properties["ca_3"])),
                "sort_order": 30,
            }
        )
    return sections


def refresh_instructor_section(course: dict[str, Any]) -> None:
    instructor = course.get("instructor", {})
    if not instructor:
        return
    lines = []
    if instructor.get("full_name") and instructor.get("full_name") != "Kean Instructor":
        lines.append(f"Instructor Name: {instructor['full_name']}")
    office = str(instructor.get("office") or "")
    if ";" in office:
        location, hours = [part.strip() for part in office.split(";", 1)]
        if location:
            lines.append(f"Office Location: {location}")
        if hours:
            hours = normalize_instructor_hours(hours)
            lines.append(f"Office Hours: {hours}")
    elif office:
        lines.append(f"Office Location: {office}")
    if instructor.get("email"):
        lines.append(f"Email: {instructor['email']}")
    if not lines:
        return
    section = {"heading": "Instructor Information", "body": "\n".join(lines), "sort_order": 20}
    existing = course.get("syllabus", {}).get("sections", [])
    course["syllabus"]["sections"] = merge_named_sections([section], existing)


def normalize_instructor_hours(value: str) -> str:
    return re.sub(r"\s*每\s*", " - ", value).replace("  ", " ").strip()


def merge_named_sections(preferred: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for section in existing:
        merged[str(section.get("heading", "")).strip().lower()] = section
    for section in preferred:
        merged[str(section.get("heading", "")).strip().lower()] = section
    order = {
        "class information": 10,
        "instructor information": 20,
        "course description from catalog": 30,
        "required texts and materials": 40,
        "optional texts and materials": 45,
        "topics and assignments": 50,
        "grading and assessment": 60,
        "grading": 60,
    }
    return sorted(merged.values(), key=lambda section: order.get(str(section.get("heading", "")).strip().lower(), section.get("sort_order", 999)))


def filter_detail_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = (
        "class information",
        "instructor information",
        "course description from catalog",
        "required texts and materials",
        "texts and materials",
        "topics and assignments",
        "grading",
    )
    filtered = []
    for section in sections:
        heading = str(section.get("heading", "")).strip()
        normalized = heading.lower()
        if any(label in normalized for label in wanted):
            filtered.append(section)
    return filtered or sections[:8]


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_dicts(child))
    return found


def extract_doc_code(source_url: str, item: dict[str, Any] | None = None) -> str | None:
    parsed = urlparse(source_url)
    path_match = DOC_VIEW_RE.search(parsed.path)
    if path_match:
        return path_match.group(1)
    query_code = parse_qs(parsed.query).get("code", [None])[0]
    if query_code:
        return query_code
    if item:
        for key in ("docCode", "doc_code", "documentCode", "document_code", "syllabusCode", "syllabus_code", "doc_code_or_id"):
            value = item.get(key)
            if value:
                return str(value)
        for key in ("docUrl", "url", "href", "link", "permalink"):
            value = item.get(key)
            if value:
                nested = extract_doc_code(str(value))
                if nested:
                    return nested
        value = item.get("code")
        if value and not COURSE_CODE_RE.search(str(value)):
            return str(value)
    return None


def extract_doc_slug(source_url: str, item: dict[str, Any] | None = None) -> str | None:
    path_match = DOC_VIEW_RE.search(urlparse(source_url).path)
    if path_match:
        return path_match.group(2)
    if item:
        for key in ("docSlug", "doc_slug", "slug", "documentSlug", "courseSlug", "course_slug"):
            value = item.get(key)
            if value and COURSE_SLUG_RE.search(str(value)):
                return COURSE_SLUG_RE.search(str(value)).group(0).upper()
        for key in ("docUrl", "url", "href", "link", "permalink"):
            value = item.get(key)
            if value:
                nested = extract_doc_slug(str(value))
                if nested:
                    return nested
        text = " ".join(str(value) for value in item.values() if isinstance(value, (str, int, float)))
        match = COURSE_SLUG_RE.search(text)
        if match:
            return match.group(0).upper()
    return None


def extract_entity_id(source_url: str, item: dict[str, Any] | None = None) -> str | None:
    parsed = urlparse(source_url)
    query_entity_id = parse_qs(parsed.query).get("entity_id", [None])[0]
    if query_entity_id:
        return query_entity_id
    if not item:
        return None
    for key in ("entity_id", "entityId"):
        value = item.get(key)
        if value:
            return str(value)
    if item.get("entity_type") == "section" and item.get("id"):
        return str(item["id"])
    entity = item.get("entity")
    if isinstance(entity, dict) and entity.get("id"):
        return str(entity["id"])
    return None


def course_from_item(item: dict[str, Any], *, source_url: str) -> dict[str, Any] | None:
    if not is_course_candidate(item):
        return None
    slug_data = parse_course_slug(extract_doc_slug(source_url, item) or "")
    properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    subject = clean_subject(first_value(item, "subject", "subjectCode", "subject_code", "department")) or clean_subject(
        first_value(properties, "subject_name", "subjectName", "subject")
    )
    number = clean_number(first_value(item, "courseNumber", "course_number", "number", "catalogNumber")) or clean_number(
        first_value(properties, "course_number", "courseNumber", "number", "catalogNumber")
    )
    code_text = preferred_value(item, "courseCode", "course_code", "title", "name", "full_name", "code")
    parsed_subject, parsed_number = parse_course_code(str(code_text or ""))
    listed_subject, listed_number, listed_section = parse_course_listing(str(code_text or ""))
    subject = subject or parsed_subject or slug_data.get("subject")
    number = number or parsed_number or slug_data.get("number")
    subject = subject or listed_subject
    number = number or listed_number
    if not subject or not number:
        return None

    section = str(first_value(item, "section", "sectionNumber", "section_number") or slug_data.get("section") or listed_section or "")
    title = (
        first_value(item, "courseTitle", "course_title", "subtitle", "sub_title", "documentTitle", "document_title", "full_name", "title", "name")
        or first_value(properties, "title", "full_name", "name")
        or f"Imported Syllabus {subject} {number}"
    )
    title = strip_course_code(str(title), subject, number)
    if not title or title == f"{subject} {number}" or is_weak_title(title, subject, number, section):
        fallback_title = first_value(item, "subtitle", "sub_title", "course_name", "courseName")
        title = str(fallback_title).strip() if fallback_title else f"Imported Syllabus {subject} {number}"

    term_code, term_name = parse_term(item)
    if term_code == "IMPORTED" and isinstance(item.get("term"), dict):
        term_code, term_name = parse_term(item["term"])
    if slug_data.get("term"):
        previous_term_code = term_code
        term_code = str(slug_data["term"])
        term_name = str(slug_data["term"]) if previous_term_code == "IMPORTED" else term_name
    instructor = instructor_from_item(item)
    doc_code = extract_doc_code(source_url, item)
    doc_slug = extract_doc_slug(source_url, item) or (
        f"{term_code}-{subject}-{number}-{section}".upper() if term_code != "IMPORTED" and section else None
    )
    doc_url = (
        f"https://kean.simplesyllabus.com/en-US/doc/{doc_code}/{doc_slug}?mode=view"
        if doc_code and doc_slug
        else None
    )
    summary = first_value(item, "summary", "description", "courseDescription", "catalogDescription") or (
        f"Imported from Kean Simple Syllabus response {source_url}."
    )
    updated_at = as_iso_datetime(first_value(item, "updated_at", "updatedAt", "modifiedAt", "lastModified", "updated"))
    enrolled = is_my_courses_search_url(source_url)

    return {
        "_term_code": term_code,
        "_term_name": term_name,
        "enrolled": enrolled,
        "subject": subject,
        "course_number": number,
        "section": section,
        "title": str(title).strip(),
        "campus": "Wenzhou-Kean University",
        "simple_syllabus_doc_code": doc_code,
        "simple_syllabus_url": doc_url,
        "term_external_id": str(item.get("term_id") or ""),
        "entity_external_id": str(item.get("entity_id") or ""),
        "material_count_hint": int(first_value(item, "material_count", "materialCount", "materials_count", "materialsCount") or 0),
        "instructor": instructor,
        "syllabus": {
            "source_label": "Kean Simple Syllabus",
            "status": "not_reviewed",
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "summary": str(summary).strip(),
            "sections": sections_from_item(item, source_url=source_url),
        },
    }


def sections_from_item(item: dict[str, Any], *, source_url: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, child in enumerate(iter_dicts(item)):
        heading = first_value(child, "heading", "header", "title", "name", "sectionTitle")
        body = first_value(child, "body", "content", "text", "description", "html")
        if heading and body and len(str(body).strip()) > 20 and is_valid_section(heading, body):
            sections.append(
                {
                    "heading": str(heading).strip()[:160],
                    "body": normalize_body(str(body)),
                    "sort_order": (index + 1) * 10,
                }
            )
    if sections:
        return sections[:12]
    return []


def extract_materials(item: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = materials_from_heading_components(item)
    dom_detail = item.get("_dom_detail") if isinstance(item, dict) else None
    if isinstance(dom_detail, dict):
        materials.extend(materials_from_dom_detail(dom_detail))
    if heading_component_items(item):
        return materials
    for child in iter_dicts(item):
        if not is_material_candidate(child):
            continue
        text = json.dumps(child, default=str)
        isbn = explicit_isbn(child) or isbn_from_text(text)
        title = first_value(child, "bookTitle", "book_title", "materialTitle", "material_title", "textbookTitle", "title", "name")
        if not isbn or not title or is_bad_material_title(str(title)):
            continue
        materials.append(
            {
                "title": str(title).strip(),
                "authors": str(first_value(child, "authors", "author", "creator") or ""),
                "material_type": "textbook",
                "isbn_13": isbn if len(isbn) == 13 else None,
                "isbn_10": isbn if len(isbn) == 10 else None,
                "edition": none_if_empty(first_value(child, "edition", "bookEdition")),
                "publisher": none_if_empty(first_value(child, "publisher", "bookPublisher")),
                "requirement_status": material_requirement(child),
                "student_status": "needed",
                "legal_search_url": f"https://www.worldcat.org/search?q={isbn}",
            }
        )
    return materials


def dedupe_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for material in materials:
        key = material.get("isbn_13") or material.get("isbn_10") or material["title"]
        unique[str(key)] = material
    return list(unique.values())


def first_value(item: dict[str, Any], *names: str) -> Any:
    normalized_names = {normalize_key(name) for name in names}
    for key, value in item.items():
        if normalize_key(str(key)) in normalized_names and value not in (None, "", []):
            if isinstance(value, list):
                return ", ".join(str(entry) for entry in value if entry)
            if isinstance(value, dict):
                return first_value(value, "name", "fullName", "full_name", "title") or json.dumps(value, default=str)
            return value
    return None


def preferred_value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = first_value(item, name)
        if value not in (None, "", []):
            return value
    return None


def best_text_value(items: list[dict[str, Any]], *names: str) -> Any:
    values = [first_value(item, *names) for item in items]
    values = [value for value in values if value not in (None, "", [])]
    if not values:
        return None
    return max(values, key=lambda value: len(str(value)))


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def clean_subject(value: Any) -> str | None:
    if not value:
        return None
    match = re.search(r"[A-Z]{2,5}", str(value).upper())
    return match.group(0) if match else None


def clean_number(value: Any) -> str | None:
    if not value:
        return None
    match = re.search(r"\d{3,4}[A-Z]?", str(value).upper())
    return match.group(0) if match else None


def parse_course_code(value: str) -> tuple[str | None, str | None]:
    match = COURSE_CODE_RE.search(value)
    if not match:
        return None, None
    return match.group(1).upper(), match.group(2).upper()


def parse_course_listing(value: str) -> tuple[str | None, str | None, str | None]:
    match = COURSE_LISTING_RE.search(value)
    if not match:
        return None, None, None
    return match.group(1).upper(), match.group(2).upper(), match.group(3).upper()


def strip_course_code(title: str, subject: str, number: str) -> str:
    return re.sub(
        rf"^\s*{re.escape(subject)}\s*[- ]?\s*{re.escape(number)}(?:[\s-]+{SECTION_CODE_PATTERN})?\s*[:\-·|]*\s*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()


def parse_term(item: dict[str, Any]) -> tuple[str, str]:
    value = first_value(item, "termCode", "term_code", "term", "termName", "term_name", "academicTerm", "semester", "name")
    if value:
        text = str(value)
        match = TERM_CODE_RE.search(text)
        if match:
            code = re.sub(r"\s+", "", match.group(1).upper())
            return code[:16], text[:120]
        return slug_term(text), text[:120]
    return "IMPORTED", "Imported Simple Syllabus"


def slug_term(value: str) -> str:
    slug = re.sub(r"[^A-Z0-9]", "", value.upper())
    return slug[:16] or "IMPORTED"


def instructor_from_item(item: dict[str, Any]) -> dict[str, Any]:
    email = email_from_text(json.dumps(item, default=str))
    office = None
    department = None
    editor_name = instructor_from_editor_list(item.get("editors"))
    if editor_name:
        return {
            "full_name": editor_name,
            "email": email,
            "department": None,
            "office": None,
        }
    for child in iter_dicts(item):
        office = office or first_value(child, "office", "officeLocation", "office_location", "location")
        department = department or first_value(child, "department", "dept", "division", "school")
        email = email or email_from_text(json.dumps(child, default=str))
        value = first_value(
            child,
            "instructor",
            "instructors",
            "faculty",
            "professor",
            "teacher",
            "primaryInstructor",
            "primary_instructor",
        )
        name = clean_instructor_name(value)
        if name:
            return {
                "full_name": name,
                "email": email,
                "department": clean_optional_text(department, limit=120),
                "office": clean_optional_text(office, limit=120),
            }
        name = clean_instructor_name(first_value(child, "fullName", "full_name", "displayName", "display_name"))
        if name and looks_like_instructor_record(child):
            return {
                "full_name": name,
                "email": email,
                "department": clean_optional_text(department, limit=120),
                "office": clean_optional_text(office, limit=120),
            }
    return {
        "full_name": "Kean Instructor",
        "email": email,
        "department": clean_optional_text(department, limit=120),
        "office": clean_optional_text(office, limit=120),
    }


def instructor_from_editor_list(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    names = [clean_instructor_name(entry.get("full_name") or entry.get("name")) for entry in value if isinstance(entry, dict)]
    names = [name for name in names if name]
    return ", ".join(names)[:120] if names else None


def parse_course_slug(value: str) -> dict[str, str]:
    match = COURSE_SLUG_RE.search(value or "")
    if not match:
        return {}
    return {
        "term": match.group("term").upper(),
        "subject": match.group("subject").upper(),
        "number": match.group("number").upper(),
        "section": match.group("section").upper(),
    }


def build_course_slug(course: dict[str, Any] | None) -> str | None:
    if not course:
        return None
    term = str(course.get("_term_code") or "").upper()
    subject = str(course.get("subject") or "").upper()
    number = str(course.get("course_number") or "").upper()
    section = str(course.get("section") or "").upper()
    if not term or term == "IMPORTED" or not subject or not number or not section:
        return None
    return f"{term}-{subject}-{number}-{section}"


def is_weak_title(title: str, subject: str, number: str, section: str = "") -> bool:
    normalized = str(title or "").strip()
    if not normalized:
        return True
    upper = normalized.upper()
    blocked = {
        "INSTRUCTOR",
        "KEAN INSTRUCTOR",
        str(section).upper(),
        f"{subject.upper()} {number.upper()}",
        f"{subject.upper()}-{number.upper()}",
    }
    if upper in blocked:
        return True
    if TERM_CODE_RE.fullmatch(upper) or COURSE_SLUG_RE.fullmatch(upper):
        return True
    return False


def is_course_candidate(item: dict[str, Any]) -> bool:
    keys = {normalize_key(str(key)) for key in item.keys()}
    if keys & {
        "coursecode",
        "course_code",
        "coursetitle",
        "course_title",
        "coursenumber",
        "course_number",
        "subjectcode",
        "subject_name",
        "doccode",
        "documentcode",
        "termname",
        "term_name",
    }:
        return True
    if item.get("entity_type") in {"section", "course"} and (item.get("title") or item.get("full_name")) and (
        "term" in item or "term_id" in item or "properties" in item or "sub_title" in item
    ):
        return True
    if isinstance(item.get("properties"), dict):
        return True
    return bool(extract_doc_slug("", item))


def is_valid_section(heading: Any, body: Any) -> bool:
    heading_text = normalize_body(str(heading)).strip()
    body_text = normalize_body(str(body)).strip()
    if not heading_text or len(body_text) < 20:
        return False
    if heading_text.lower() in SKIPPED_SECTION_HEADINGS:
        return False
    body_lower = body_text.lower()
    return not any(marker in body_lower for marker in SYSTEM_SECTION_MARKERS)


def first_dom_detail(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in items:
        detail = item.get("_dom_detail") if isinstance(item, dict) else None
        if isinstance(detail, dict):
            return detail
        if isinstance(item, dict) and "visibleText" in item and ("sections" in item or "tableRows" in item):
            return item
    return None


def sections_from_dom_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(detail.get("sections", [])):
        if not isinstance(section, dict):
            continue
        heading = section.get("heading")
        body = section.get("body")
        if not is_valid_section(heading, body):
            continue
        sections.append(
            {
                "heading": str(heading).strip()[:160],
                "body": normalize_body(str(body)),
                "sort_order": (index + 1) * 10,
            }
        )
    return sections[:20]


def sections_from_heading_components(item: dict[str, Any]) -> list[dict[str, Any]]:
    components = heading_component_items(item)
    sections: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        name = component_name(component)
        if not name:
            continue
        body_parts = component_text_parts(component)
        if rows := rows_as_text(component):
            body_parts.extend(rows)
        body = " ".join(part for part in body_parts if part)
        if not is_valid_section(name, body):
            continue
        sections.append(
            {
                "heading": name[:160],
                "body": normalize_body(body),
                "sort_order": (index + 1) * 10,
            }
        )
    return sections[:24]


def materials_from_heading_components(item: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for component in heading_component_items(item):
        name = component_name(component)
        if not name or "material" not in name.lower():
            continue
        requirement = "optional" if "optional" in name.lower() else "required"
        columns = component_column_map(component)
        for row in component.get("rows") or []:
            cells = row.get("cells") or []
            if not cells:
                continue
            row_values: dict[str, str] = {}
            for index, cell in enumerate(cells):
                column = columns.get(str(cell.get("column_id"))) or f"column_{index}"
                value = clean_optional_text(variation_value(cell.get("variation") or {}), limit=500)
                if value:
                    row_values[normalize_key(column)] = value
            title = first_present(row_values, "title", "booktitle", "materialtitle")
            isbn = normalize_isbn_value(first_present(row_values, "isbn", "isbn13", "isbn10"))
            if not title or is_bad_material_title(title):
                continue
            materials.append(
                {
                    "title": title,
                    "authors": first_present(row_values, "authors", "author") or "",
                    "material_type": "textbook",
                    "isbn_13": isbn if isbn and len(isbn) == 13 else None,
                    "isbn_10": isbn if isbn and len(isbn) == 10 else None,
                    "edition": first_present(row_values, "edition"),
                    "publisher": first_present(row_values, "publisher"),
                    "requirement_status": requirement,
                    "student_status": "needed",
                    "legal_search_url": f"https://www.worldcat.org/search?q={isbn or title}",
                }
            )
    return materials


def materials_from_dom_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for row in detail.get("tableRows", []):
        if not isinstance(row, list) or len(row) < 2:
            continue
        row_text = " ".join(str(cell) for cell in row)
        isbn = isbn_from_text(row_text)
        if not isbn:
            continue
        title = next((str(cell) for cell in row if not isbn_from_text(str(cell)) and not is_bad_material_title(str(cell))), None)
        if not title:
            continue
        materials.append(
            {
                "title": title,
                "authors": "",
                "material_type": "textbook",
                "isbn_13": isbn if isbn and len(isbn) == 13 else None,
                "isbn_10": isbn if isbn and len(isbn) == 10 else None,
                "requirement_status": "required",
                "student_status": "needed",
                "legal_search_url": f"https://www.worldcat.org/search?q={isbn or title}",
            }
        )
    return materials


def heading_component_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    if "variations" in item and ("rows" in item or "columns" in item):
        return [item]
    components = []
    for child in item.get("items", []) if isinstance(item.get("items"), list) else []:
        if isinstance(child, dict) and "variations" in child:
            components.append(child)
    return components


def component_name(component: dict[str, Any]) -> str | None:
    for variation in component.get("variations") or []:
        for source in ("combined", "published", "child", "parent"):
            payload = variation.get(source)
            if isinstance(payload, dict) and not payload.get("is_deleted") and payload.get("name"):
                return normalize_body(str(payload["name"]))
    return None


def component_text_parts(component: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for variation in component.get("variations") or []:
        for source in ("combined", "published", "child", "parent"):
            payload = variation.get(source)
            if not isinstance(payload, dict) or payload.get("is_deleted") or payload.get("is_visible") is False:
                continue
            for key in ("header", "value", "footer", "description"):
                value = clean_optional_text(payload.get(key), limit=3000)
                if value and value not in parts:
                    parts.append(value)
            break
    return parts


def component_columns(component: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    for column in component.get("columns") or []:
        variation = column.get("variation") or {}
        name = variation_name(variation)
        columns.append(name or f"column_{len(columns)}")
    return columns


def component_column_map(component: dict[str, Any]) -> dict[str, str]:
    columns: dict[str, str] = {}
    for index, column in enumerate(component.get("columns") or []):
        heading = column.get("heading") if isinstance(column, dict) else None
        column_id = heading.get("id") if isinstance(heading, dict) else None
        variation = column.get("variation") or {}
        name = variation_name(variation) or f"column_{index}"
        if column_id:
            columns[str(column_id)] = name
    return columns


def rows_as_text(component: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    columns = component_column_map(component)
    for row in component.get("rows") or []:
        values: list[str] = []
        for index, cell in enumerate(row.get("cells") or []):
            value = clean_optional_text(variation_value(cell.get("variation") or {}), limit=600)
            if not value:
                continue
            label = columns.get(str(cell.get("column_id"))) or ""
            values.append(f"{label}: {value}" if label else value)
        if values:
            rows.append("; ".join(values))
    return rows


def variation_payload(variation: dict[str, Any]) -> dict[str, Any] | None:
    for source in ("combined", "published", "child", "parent"):
        payload = variation.get(source)
        if isinstance(payload, dict) and not payload.get("is_deleted") and payload.get("is_visible") is not False:
            return payload
    return None


def variation_name(variation: dict[str, Any]) -> str | None:
    payload = variation_payload(variation)
    return clean_optional_text(payload.get("name"), limit=120) if payload else None


def variation_value(variation: dict[str, Any]) -> str | None:
    payload = variation_payload(variation)
    if not payload:
        return None
    return clean_optional_text(payload.get("value") or payload.get("header") or payload.get("footer"), limit=500)


def instructor_from_dom_detail(detail: dict[str, Any]) -> dict[str, Any]:
    text = str(detail.get("visibleText") or "")
    email = email_from_text(text)
    name = text_after_label(text, ("Instructor", "Professor", "Faculty"))
    office = text_after_label(text, ("Office", "Office Location", "Room"))
    department = text_after_label(text, ("Department", "School"))
    return {
        "full_name": clean_instructor_name(name) or "Kean Instructor",
        "email": email,
        "department": clean_optional_text(department, limit=120),
        "office": clean_optional_text(office, limit=120),
    }


def instructor_from_heading_components(item: dict[str, Any]) -> dict[str, Any]:
    for component in heading_component_items(item):
        name = component_name(component)
        if not name or "instructor" not in name.lower():
            continue
        columns = component_column_map(component)
        for row in component.get("rows") or []:
            values: dict[str, str] = {}
            cell_values: list[str] = []
            for index, cell in enumerate(row.get("cells") or []):
                column = columns.get(str(cell.get("column_id"))) or f"column_{index}"
                value = clean_optional_text(variation_value(cell.get("variation") or {}), limit=500)
                if value:
                    values[normalize_key(column)] = value
                    cell_values.append(value)
            full_name = clean_instructor_name(first_present(values, "instructorname", "name", "fullname"))
            if not full_name:
                full_name = first_instructor_like_value(cell_values)
            if not full_name:
                continue
            email = first_present(values, "email") or next((value for value in cell_values if email_from_text(value)), None)
            if email:
                email = email_from_text(email) or email
            office = first_present(values, "officelocation", "office", "room") or first_office_like_value(cell_values)
            office_hours = first_present(values, "officehours") or first_office_hours_like_value(cell_values)
            if office and office_hours:
                office = f"{office}; {office_hours}"
            return {
                "full_name": full_name,
                "email": email or email_from_text(json.dumps(values, default=str)),
                "department": first_present(values, "department"),
                "office": office,
            }
    return {"full_name": "Kean Instructor", "email": None, "department": None, "office": None}


def merge_instructor(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_name = existing.get("full_name")
    incoming_name = incoming.get("full_name")
    full_name = incoming_name or existing_name or "Kean Instructor"
    if (
        full_name == "Kean Instructor"
        or (existing_name and existing_name != "Kean Instructor" and incoming_name and not looks_like_person_name(incoming_name))
    ) and existing_name:
        full_name = existing["full_name"]
    return {
        "full_name": full_name,
        "email": incoming.get("email") or existing.get("email"),
        "department": incoming.get("department") or existing.get("department"),
        "office": incoming.get("office") or existing.get("office"),
    }


def looks_like_person_name(value: str) -> bool:
    text = normalize_body(value)
    if not text or email_from_text(text):
        return False
    if first_office_like_value([text]) or first_office_hours_like_value([text]):
        return False
    return bool(re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", text))


def looks_like_instructor_record(item: dict[str, Any]) -> bool:
    keys = {normalize_key(str(key)) for key in item.keys()}
    return bool(keys & {"instructor", "instructors", "faculty", "professor", "teacher", "email", "office"})


def clean_instructor_name(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        value = first_value(value, "fullName", "full_name", "displayName", "display_name", "name", "title")
    text = normalize_body(str(value))
    if not text or len(text) > 120:
        return None
    lower = text.lower()
    if any(marker in lower for marker in SYSTEM_SECTION_MARKERS):
        return None
    if lower in SKIPPED_SECTION_HEADINGS or COURSE_CODE_RE.search(text):
        return None
    if first_office_like_value([text]) or first_office_hours_like_value([text]):
        return None
    return text


def first_instructor_like_value(values: list[str]) -> str | None:
    for value in values:
        if email_from_text(value):
            continue
        cleaned = clean_instructor_name(value)
        if cleaned and not first_office_hours_like_value([cleaned]) and not first_office_like_value([cleaned]):
            return cleaned
    return None


def first_office_like_value(values: list[str]) -> str | None:
    for value in values:
        text = normalize_body(value)
        if re.search(r"\b[A-Z]{2,5}\s*[A-Z]?\d{2,4}\b", text) or re.search(r"\b(room|office|building)\b", text, re.IGNORECASE):
            return text
    return None


def first_office_hours_like_value(values: list[str]) -> str | None:
    for value in values:
        text = normalize_body(value)
        if re.search(r"\b(mon|tue|wed|thu|fri|monday|tuesday|wednesday|thursday|friday)\b", text, re.IGNORECASE) and re.search(
            r"\d{1,2}:\d{2}", text
        ):
            return text
    return None


def email_from_text(value: str) -> str | None:
    match = EMAIL_RE.search(value or "")
    return match.group(0) if match else None


def text_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*:?\s*([^\n\r|]+)", re.IGNORECASE)
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip()
    return None


def is_material_candidate(item: dict[str, Any]) -> bool:
    keys = " ".join(normalize_key(str(key)) for key in item.keys())
    if any(keyword in keys for keyword in MATERIAL_KEYWORDS):
        return True
    return False


def explicit_isbn(item: dict[str, Any]) -> str | None:
    value = first_value(item, "isbn_13", "isbn13", "isbn_10", "isbn10", "isbn")
    return normalize_isbn_value(value)


def isbn_from_text(text: str) -> str | None:
    match = ISBN_RE.search(text or "")
    return normalize_isbn_value(match.group(0)) if match else None


def normalize_isbn_value(value: Any) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", str(value)).upper()
    return normalized if len(normalized) in {10, 13} else None


def material_requirement(item: dict[str, Any]) -> str:
    text = json.dumps(item, default=str).lower()
    if "optional" in text or "recommended" in text:
        return "optional"
    return "required"


def is_bad_material_title(value: str) -> bool:
    title = normalize_body(value)
    upper = title.upper()
    if len(title) < 4:
        return True
    if upper in {"INSTRUCTOR", "REQUIRED", "OPTIONAL", "MATERIALS", "TEXTBOOK", "NONE"}:
        return True
    if TERM_CODE_RE.fullmatch(upper) or COURSE_SLUG_RE.fullmatch(upper):
        return True
    if re.fullmatch(r"[A-Z]?\d{1,3}[A-Z]?", upper):
        return True
    if re.fullmatch(r"[A-Z]{2,6}\s+\d{3,4}[A-Z]?\s+[A-Z]?\d{1,3}[A-Z]?", upper):
        return True
    return any(marker in title.lower() for marker in SYSTEM_SECTION_MARKERS)


def clean_optional_text(value: Any, *, limit: int) -> str | None:
    if not value:
        return None
    text = normalize_body(str(value))
    return text[:limit] if text else None


def none_if_empty(value: Any) -> str | None:
    text = clean_optional_text(value, limit=120)
    return text or None


def first_present(values: dict[str, str], *keys: str) -> str | None:
    normalized = {normalize_key(key) for key in keys}
    for key, value in values.items():
        if normalize_key(key) in normalized and value:
            return value
    return None


def normalize_body(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000] or "Imported section content."


def as_iso_datetime(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return None
