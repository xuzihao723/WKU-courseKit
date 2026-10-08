from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.schemas import SimpleSyllabusExportPayload
from app.services.importer import load_syllabus_payload


SIMPLE_SYLLABUS_LIBRARY_URL = "https://kean.simplesyllabus.com/en-US/syllabus-library"
SIMPLE_SYLLABUS_MY_COURSES_URL = "https://kean.simplesyllabus.com/en-US/syllabus-library/my-courses"


class SimpleSyllabusImportError(ValueError):
    pass


@dataclass(frozen=True)
class SimpleSyllabusSettings:
    authorize_url: str | None
    token_url: str | None
    api_urls: tuple[str, ...]
    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    scope: str

    @property
    def oauth_ready(self) -> bool:
        return bool(self.authorize_url and self.client_id and self.redirect_uri)

    @property
    def sync_ready(self) -> bool:
        return bool(self.oauth_ready and self.token_url and self.api_urls)

    def authorization_url(self, *, state: str = "wkcoursekit") -> str | None:
        if not self.oauth_ready:
            return None
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
        }
        return f"{self.authorize_url}?{urlencode(params)}"


def get_simple_syllabus_settings() -> SimpleSyllabusSettings:
    return SimpleSyllabusSettings(
        authorize_url=getenv("SIMPLE_SYLLABUS_AUTHORIZE_URL"),
        token_url=getenv("SIMPLE_SYLLABUS_TOKEN_URL"),
        api_urls=parse_api_urls(),
        client_id=getenv("SIMPLE_SYLLABUS_CLIENT_ID"),
        client_secret=getenv("SIMPLE_SYLLABUS_CLIENT_SECRET"),
        redirect_uri=getenv("SIMPLE_SYLLABUS_REDIRECT_URI"),
        scope=getenv("SIMPLE_SYLLABUS_SCOPE", "syllabus-library my-courses"),
    )


def official_links() -> dict[str, str]:
    return {
        "library": SIMPLE_SYLLABUS_LIBRARY_URL,
        "my_courses": SIMPLE_SYLLABUS_MY_COURSES_URL,
    }


def parse_api_urls() -> tuple[str, ...]:
    urls = getenv("SIMPLE_SYLLABUS_API_URLS")
    if not urls:
        urls = ",".join(
            value
            for value in (
                getenv("SIMPLE_SYLLABUS_MY_COURSES_API_URL"),
                getenv("SIMPLE_SYLLABUS_LIBRARY_API_URL"),
            )
            if value
        )
    return tuple(url.strip() for url in urls.split(",") if url.strip()) if urls else ()


def import_from_authorization_code(
    db: Session,
    code: str,
    *,
    settings: SimpleSyllabusSettings | None = None,
    reset: bool = True,
) -> dict[str, int]:
    active_settings = settings or get_simple_syllabus_settings()
    if not active_settings.sync_ready:
        raise SimpleSyllabusImportError(
            "Automatic sync requires approved OAuth token and API endpoint settings."
        )
    access_token = exchange_authorization_code(code, active_settings)
    payloads = fetch_authorized_payloads(access_token, active_settings.api_urls)
    payload = merge_official_payloads(payloads)
    return load_syllabus_payload(db, payload, reset=reset)


def exchange_authorization_code(code: str, settings: SimpleSyllabusSettings) -> str:
    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
    }
    if settings.client_secret:
        token_request["client_secret"] = settings.client_secret

    try:
        response = httpx.post(settings.token_url or "", data=token_request, timeout=15.0)
        response.raise_for_status()
        token_payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SimpleSyllabusImportError("Could not exchange the authorization code for an access token.") from exc

    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise SimpleSyllabusImportError("Token response did not include an access_token.")
    return access_token


def fetch_authorized_payloads(access_token: str, api_urls: tuple[str, ...]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    for api_url in api_urls:
        try:
            response = httpx.get(api_url, headers=headers, timeout=30.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SimpleSyllabusImportError(f"Could not fetch Simple Syllabus data from {api_url}.") from exc
        if not isinstance(payload, dict):
            raise SimpleSyllabusImportError(f"Simple Syllabus API response from {api_url} was not a JSON object.")
        payloads.append(payload)
    return payloads


def merge_official_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        raise SimpleSyllabusImportError("No Simple Syllabus API responses were returned.")

    merged: dict[str, Any] = {"student_key": payloads[0].get("student_key", "kean-student"), "terms": []}
    terms_by_code: dict[str, dict[str, Any]] = {}

    for payload in payloads:
        validated = validate_payload(payload)
        if validated.get("student_key"):
            merged["student_key"] = validated["student_key"]
        for term in validated.get("terms", []):
            term_code = term["code"]
            existing = terms_by_code.get(term_code)
            if existing is None:
                existing = {**term, "courses": []}
                terms_by_code[term_code] = existing
                merged["terms"].append(existing)
            existing_courses = {
                course_identity(course): course
                for course in existing.get("courses", [])
            }
            for course in term.get("courses", []):
                existing_courses[course_identity(course)] = course
            existing["courses"] = list(existing_courses.values())

    return merged


def course_identity(course: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(course.get("subject", "")).upper(),
        str(course.get("course_number", "")),
        str(course.get("section", "")),
    )


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = SimpleSyllabusExportPayload.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first_error.get("loc", []))
        message = first_error.get("msg", "Payload does not match the expected export shape.")
        detail = f"{location}: {message}" if location else message
        raise SimpleSyllabusImportError(detail) from exc

    return validated.model_dump(exclude_none=True)
