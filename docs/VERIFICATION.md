# Runtime verification

Verification date: 2026-10-08 (Asia/Shanghai).

The checked workflow was: load local course records → browse/search courses → read a syllabus and materials → build a printable packet. The project was tested with its existing virtual environment on Windows using Python 3.11.9.

## Environment

| Dependency | Installed version |
| --- | --- |
| FastAPI | 0.136.3 |
| Uvicorn | 0.49.0 |
| Jinja2 | 3.1.6 |
| SQLAlchemy | 2.0.50 |
| Pydantic | 2.13.4 |
| HTTPX | 0.28.1 |
| python-multipart | 0.0.32 |
| Playwright | 1.60.0 |

`python -m pip check` reported `No broken requirements found.` Importing `app.main` succeeded. All 23 Python files under `app/` and `scripts/` passed AST parsing.

## Data isolation

The existing database was inspected read-only and backed up into a temporary application copy using SQLite's backup API. Integrity checking returned `ok`. Its local record totals were 36 courses, 8 terms, 46 materials, and 32 enrollments. No original database records were reset.

The temporary copy excluded authentication credentials and browser profiles. Core routes were first tested with the database snapshot, then with a fresh demo database created by `scripts/seed_db.py`.

## Results

| Check | Result |
| --- | --- |
| Existing-data HTTP checks | 19 requests passed: root redirect, health, courses/filtering, library/search, materials/filtering, print, CSS, auth status, material counts, and five course detail pages. |
| Demo-data HTTP checks | 13 requests passed: health, courses, library, materials, print, and all eight demo course readers. |
| Language rendering | 10 requests passed: five core pages in both English and Chinese with the expected HTML language attribute. |
| Demo import | 2 terms, 8 instructors, 8 courses, 8 syllabi, 21 sections, 7 materials, 8 course-material links, 7 student material statuses. |
| Clean publication export | Seven core page/asset checks passed using only staged repository files, without a preexisting database, credentials, or caches. First startup automatically created eight demo courses. |
| Actual server startup | Uvicorn started on a loopback port and logged `Application startup complete.` |
| Browser rendering | Chromium opened five core pages successfully. Screenshots are in `docs/images/`. |
| Search form | Submitting `CPS 3320` produced one matching course row. |
| Language selection | Chinese selection rendered the Chinese page heading and `lang="zh"`. |
| Print button | Clicking the button invoked `window.print`; the operating-system print dialog and physical printing were not exercised. |
| JavaScript errors | No `pageerror` events occurred during these core browser checks. |
| External catalog | A separate `/catalog` request returned HTTP 200 in approximately 5.3 seconds and displayed a results list. This does not establish freshness or long-term availability. |

The 42 core HTTP checks and catalog request are distinct from the additional browser navigation checks. They are a one-time smoke verification, not an exhaustive regression suite or a coverage measurement.

## Limits and observations

- The default system Python lacked Jinja2 and Playwright. The project's `.venv` had all declared packages, so README commands explicitly use that interpreter.
- TestClient emitted a deprecation warning about its HTTPX integration; it did not prevent these checks from passing.
- Authenticated Kean login, session renewal, synchronization, browser planner operations, and calendar export were not tested end to end.
- Core page tests were run without credentials. External synchronization errors/notices can coexist with usable local demo records.
- The live catalog currently targets Fall 2026 Wenzhou; its response may be served from fallback cache.
- No multi-user, public-server, load, penetration, accessibility, or cross-platform verification was performed.
- Runtime versions are reported above; `requirements.txt` contains version ranges rather than an exact dependency lock.

## Reproduce a basic check

From `WKUCourseKit_Final_Project/Code` on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Then visit `/courses`, `/library`, `/materials`, and `/print`; open a course, submit a search, switch language, and check printing. To repeat a clean-dataset check without changing personal records, use a separate clone.
