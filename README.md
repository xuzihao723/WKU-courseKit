<a id="readme-top"></a>

<div align="center">

# WKUCourseKit

**Your courses, syllabi, and study materials—in one local workspace.**

A Python web application for Wenzhou-Kean University / Kean students, built for the CPS 3320 final project.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Interface](https://img.shields.io/badge/Interface-English%20%2F%20中文-C87659)

[Quick Start](#getting-started) · [Screenshots](#screenshots) · [Report a Bug](https://github.com/xuzihao723/WKU-courseKit/issues) · [中文说明](README.zh-CN.md)

</div>

![WKUCourseKit course list using the included demonstration dataset](docs/images/courses.png)

<details>
<summary>Table of contents</summary>

- [About the project](#about-the-project)
- [Features](#features)
- [Screenshots](#screenshots)
- [Built with](#built-with)
- [Getting started](#getting-started)
- [Usage](#usage)
- [Dataset and local storage](#dataset-and-local-storage)
- [Optional Simple Syllabus import](#optional-simple-syllabus-import)
- [Project structure](#project-structure)
- [Verification and limitations](#verification-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Contact and acknowledgments](#contact-and-acknowledgments)

</details>

## About the project

Course information is often spread across syllabus pages, textbook lists, and separate planning tools. WKUCourseKit brings these records together so students can find a course, read its syllabus, check required materials, and prepare a printable packet.

The interface is rendered on the server with Jinja2, while SQLAlchemy stores course records in a local SQLite database. A bundled demonstration dataset makes the core workflow usable without a university account. Optional integrations retrieve course catalog information and import authorized Simple Syllabus records.

This repository distributes a **locally run application**. It is a student project and is not an official university service. GitHub hosts the source and project artifacts; it does not host the running Python application.

## Features

| Area | What you can do |
| --- | --- |
| My Courses | Find enrolled courses by keyword, term, subject, or material availability; sort by course code or title. |
| Syllabus Library | Search syllabus records by course code, title, instructor, term, and subject. |
| Course Reader | Read course metadata, syllabus sections, instructor details, and linked materials. |
| Materials | Review required and optional materials, ISBNs, existing student statuses, and library/bookstore links. |
| Print Center | Build a course packet and material checklist; use the browser print dialog or Save as PDF. |
| Course Catalog | Browse the external Kean catalog, currently configured for Fall 2026 Wenzhou; use the browser-based planning interface. |
| Language | Switch between English and Chinese interface text. |
| Optional import | Sign in directly to Simple Syllabus through a local browser and import authorized records. |

## Screenshots

These screenshots were captured from the bundled demo dataset, without a signed-in university session.

| Syllabus Library | Course Reader |
| --- | --- |
| ![Syllabus library](docs/images/library.png) | ![Course reader](docs/images/course-detail.png) |

<details>
<summary>Materials and Print Center</summary>

![Materials and ISBN records](docs/images/materials.png)

![Printable syllabus packet](docs/images/print.png)

</details>

## Built with

| Component | Technology |
| --- | --- |
| Web application | Python, FastAPI, Uvicorn |
| Pages and styling | Jinja2, HTML, CSS, browser JavaScript |
| Data storage | SQLite, SQLAlchemy |
| Validation and HTTP | Pydantic, HTTPX, python-multipart |
| Optional browser import | Playwright |

Dependencies are listed in [requirements.txt](WKUCourseKit_Final_Project/Code/requirements.txt). There is no Node.js build step or separate database server.

## Getting started

### Prerequisites

- **Python 3.11** is the verified runtime.
- Git, or download and extract this repository using **Code → Download ZIP**.
- Internet access for the initial dependency installation; the catalog and university import also need a network connection.

### Windows / PowerShell

```powershell
git clone https://github.com/xuzihao723/WKU-courseKit.git
cd WKU-courseKit\WKUCourseKit_Final_Project\Code

py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If `py` is unavailable, use `python -m venv .venv` after confirming `python --version`. Calling the virtual environment's Python directly avoids PowerShell activation-policy problems.

### macOS / Linux

```bash
git clone https://github.com/xuzihao723/WKU-courseKit.git
cd WKU-courseKit/WKUCourseKit_Final_Project/Code

python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The commands above describe the equivalent setup; the verification run was performed on Windows.

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. The first startup creates the database and loads demo records when the course table is empty. No `.env` file, API key, or university login is needed for the demo. Stop the server with **Ctrl+C**.

For development, append `--reload` to the Uvicorn command.

## Usage

1. Open **My Courses** and search for `CPS 3320`.
2. Select a course to read its syllabus and review materials.
3. Use **Syllabus Library** to search across the available records.
4. Open **Materials** to see required/optional items, ISBNs, and source links.
5. Open **Print Center**, choose a term, and print or save the packet as PDF.
6. Use **EN / 中文** in the navigation to change the interface language.

| Route | Purpose |
| --- | --- |
| `/` | Redirect to My Courses |
| `/courses` | Enrolled course list |
| `/library` | Syllabus search |
| `/courses/{course_id}` | Course reader; `/courses/1` works with the demo dataset |
| `/materials` | Material list |
| `/print` | Printable packet and checklist |
| `/catalog` | External course catalog |
| `/health` | Application/database health check |

A healthy server returns the following from `/health`:

```json
{"status":"ok","database":"connected"}
```

## Dataset and local storage

The runtime dataset is [Code/data/mock_syllabus.json](WKUCourseKit_Final_Project/Code/data/mock_syllabus.json); a submission copy and description are available in [Dataset](WKUCourseKit_Final_Project/Dataset/DATASET.md).

| Record | Demo count |
| --- | ---: |
| Terms | 2 |
| Instructors | 8 |
| Courses / syllabi | 8 / 8 |
| Syllabus sections | 21 |
| Materials | 7 |
| Course-material links | 8 |
| Student material statuses | 7 |

To explicitly restore the demo dataset, stop the server, back up `Code/wkcoursekit.db` if needed, and run this from `Code`:

```powershell
.\.venv\Scripts\python.exe scripts\seed_db.py
```

**This command clears existing application records before importing the demo.** On macOS/Linux, use `.venv/bin/python scripts/seed_db.py`.

The database, `.env`, session files, browser profiles, imported catalog caches, and logs stay local and are excluded by [.gitignore](.gitignore). The JSON demo does not include student passwords or textbook files.

## Optional Simple Syllabus import

Run locally from `Code`:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\sync_simple_syllabus.py
```

Complete the university login yourself in the browser opened by the script. The importer uses that authorized browser session; the application does not ask you to enter your university password into its own pages. Session cookies/tokens and browser state can be stored locally, so keep those files private.

Back up the local database before syncing: the import flow can replace existing records. This optional integration depends on university access, session validity, and the current external website. The authenticated login/import flow was **not verified** in the publication check.

## Project structure

```text
WKU-courseKit/
├── README.md
├── README.zh-CN.md
├── docs/
│   ├── images/                       Demo screenshots
│   └── VERIFICATION.md               Verification evidence and scope
└── WKUCourseKit_Final_Project/
    ├── Code/
    │   ├── app/
    │   │   ├── main.py               FastAPI entry point and startup
    │   │   ├── database.py           SQLite initialization
    │   │   ├── models.py             SQLAlchemy models
    │   │   ├── routes/               Page and API routes
    │   │   ├── services/             Search, import, catalog, materials
    │   │   ├── templates/            Jinja2 templates
    │   │   └── static/css/           Stylesheets
    │   ├── data/mock_syllabus.json
    │   ├── scripts/                  Seed and optional synchronization
    │   └── requirements.txt
    ├── Dataset/                      Dataset documentation and copy
    ├── README/                       Submission setup notes
    ├── Results/                      Original screenshots and summary
    ├── Report PDF/                   Final project report
    └── Presentation Slides/          Final presentation
```

[Final report (PDF)](WKUCourseKit_Final_Project/Report%20PDF/WKUCourseKit_Final_Report.pdf) · [Presentation (PPTX)](WKUCourseKit_Final_Project/Presentation%20Slides/WKUCourseKit_Final_Presentation.pptx) · [Results](WKUCourseKit_Final_Project/Results/docs/RESULTS.md)

## Verification and limitations

The publication check used Windows and Python 3.11.9. Dependencies passed `pip check`, all 23 Python source files parsed successfully, and 42 core route/language checks passed against isolated copies of existing and demo data. Five core pages were also checked in Chromium; search submission, Chinese UI selection, and the print-button handler passed with no JavaScript page errors. A separate catalog request returned HTTP 200 with results. See [verification details](docs/VERIFICATION.md).

- This is a local, single-user project. User accounts, multi-user data isolation, and production hosting are outside the verified scope.
- Core pages use local records; external catalog freshness and Simple Syllabus synchronization depend on remote services. A demo library page may show a sign-in/sync notice while still displaying local results.
- The course catalog currently targets **Fall 2026 Wenzhou**. It is not an automatic current-term selector.
- The catalog's browser planner and calendar export have not received end-to-end verification.
- Dependency versions use ranges rather than a lockfile; other environments may resolve different versions.
- [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) is a static-site service. Running this FastAPI/SQLite application requires a Python process.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `ModuleNotFoundError` or `uvicorn` not found | Install dependencies and launch using the `.venv` Python commands above. |
| `Could not import module app.main` | Run Uvicorn from `WKUCourseKit_Final_Project/Code`. |
| Port 8000 is occupied | Start with `--port 8001`, then open `http://127.0.0.1:8001`. |
| No courses match | Clear the keyword, term, and subject filters. Only reset the dataset if you intend to discard current records. |
| Catalog is unavailable | Check internet access; previously cached results may be available. Core demo pages can still be used. |
| Simple Syllabus login fails | Install Playwright Chromium, retry the official login, and check university access. |

## Contributing

Use [Issues](https://github.com/xuzihao723/WKU-courseKit/issues) for reproducible bugs and suggestions. Include your Python/OS version, startup command, and the affected page. Remove personal session data from logs before sharing them. For a change, fork the repository, work on a branch, verify the relevant local workflow, and open a pull request.

## License

No license file is currently provided. This repository does not declare an open-source license; contact the maintainer about reuse beyond applicable permissions.

## Contact and acknowledgments

Repository maintainer: [@xuzihao723](https://github.com/xuzihao723).

README organization is inspired by [awesome-readme](https://github.com/matiassingers/awesome-readme) and [Best-README-Template](https://github.com/othneildrew/Best-README-Template). Thanks to the maintainers of FastAPI, SQLAlchemy, Jinja2, HTTPX, and Playwright. University services and third-party material links remain the property of their respective providers.

<p align="right"><a href="#readme-top">Back to top</a></p>
