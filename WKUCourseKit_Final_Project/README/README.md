# WKUCourseKit

For the maintained GitHub guide, screenshots, and verified setup commands, see the [English README](../../README.md) or [中文说明](../../README.zh-CN.md).

WKUCourseKit is a local Python web application for CPS 3320 Final Project. It helps WKU/Kean students organize enrolled courses, syllabus information, required and optional materials, ISBN records, and print-friendly course packets.

The project runs entirely on the local computer. No external hosting is required.

## Project Description

The application provides five main pages:

- **My Courses**: shows enrolled courses with search, term filter, subject filter, material count, and print action.
- **Syllabus Library**: searches all course records by course code, title, instructor, term, subject, and material availability.
- **Course Detail / Syllabus Reader**: displays course metadata, instructor information, syllabus sections, grading, schedule, policies, and materials.
- **Materials**: lists required and optional materials with ISBN values, student status, and source links.
- **Print Center**: creates print-friendly syllabus packets and material checklists.

The project is Python-first. FastAPI handles routes, SQLAlchemy manages the SQLite database, Jinja2 builds the HTML pages, and Python service modules handle data import, search, filters, material grouping, and optional Simple Syllabus synchronization.

For a repeatable final-project demo, the submission includes a local JSON dataset. The app also includes an optional browser-assisted Simple Syllabus import/scraping flow: the student signs in directly on the official Kean Simple Syllabus website, and the local app can import authorized course and syllabus records into SQLite. The app does not store Kean passwords.

## Installation Instructions

After extracting the zip file, open Windows PowerShell in:

```text
Code
```

Then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Required libraries are listed in `requirements.txt`:

- FastAPI
- Uvicorn
- Jinja2
- SQLAlchemy
- Pydantic
- httpx
- python-multipart
- Playwright

The Playwright dependency supports the optional local Simple Syllabus browser import. The included dataset does not require using that optional flow.

## How To Run The Code

Open Windows PowerShell in the `Code` folder. The first application startup automatically loads demo data into an empty database. To explicitly reset an existing database to the demo dataset, back up `wkcoursekit.db` first, then run the command below. **This command clears existing application records.**

```powershell
python scripts\seed_db.py
```

Then start the local web server:

```powershell
python -m uvicorn app.main:app --reload
```

Open this address in a browser:

```text
http://127.0.0.1:8000
```

Useful routes:

- `http://127.0.0.1:8000/courses`
- `http://127.0.0.1:8000/library`
- `http://127.0.0.1:8000/materials`
- `http://127.0.0.1:8000/print`
- `http://127.0.0.1:8000/health`

To stop the server, press `Ctrl+C` in the terminal.

Optional Simple Syllabus import:

```powershell
python -m playwright install chromium
python scripts\sync_simple_syllabus.py
```

This opens a local browser session for Kean Simple Syllabus login. After the student signs in through Kean, the script imports available authorized syllabus data into the same local SQLite database. This step is optional; the project works with `python scripts\seed_db.py`.

## Dataset Usage Explained

The dataset is included in the submission in two places:

```text
Code/data/mock_syllabus.json
Dataset/data/mock_syllabus.json
```

The copy inside `Code` is used by `scripts\seed_db.py` when running the program. The copy inside `Dataset` is included to make the dataset section easy to find for grading. It is a Simple Syllabus-style dataset for demonstration. It does not contain real student passwords, private Kean credentials, or copyrighted textbook files.

The seed script:

```powershell
python scripts\seed_db.py
```

loads the JSON data into a local SQLite database and creates:

- 2 terms
- 8 instructors
- 8 courses
- 8 syllabi
- 21 syllabus sections
- 7 materials
- 8 course-material links
- 7 student material statuses

The website then reads this database and displays the information through the course list, syllabus library, course detail, materials, and print pages. If the optional Simple Syllabus import is used, imported records are stored in the same local SQLite database and shown through the same pages.

More dataset details are in:

```text
Dataset/DATASET.md
```

## Output Explanation

The project output is the local website plus the included final artifacts.

Main website outputs:

- `/courses`: enrolled course table with filters and print selection.
- `/library`: searchable syllabus library.
- `/courses/{course_id}`: detailed syllabus reader for one course.
- `/materials`: material checklist with ISBN and source links.
- `/print`: print-friendly syllabus packet and material checklist.
- `/health`: confirms the app and database are working.

Included result files:

- `Results/01_my_courses.png`
- `Results/02_syllabus_library.png`
- `Results/03_course_detail.png`
- `Results/04_materials.png`
- `Results/05_print_center.png`

Included final documents:

- `Report PDF/WKUCourseKit_Final_Report.pdf`
- `Presentation Slides/WKUCourseKit_Final_Presentation.pptx`
- `Results/docs/RESULTS.md`

## Project Structure

```text
Code/app/                         FastAPI application code
Code/app/main.py                  Main application entry point
Code/app/routes/                  Page routes
Code/app/services/                Python service functions
Code/app/templates/               Jinja2 HTML templates
Code/app/static/css/styles.css    Main stylesheet
Code/data/mock_syllabus.json      Runtime dataset copy
Code/scripts/seed_db.py           Loads the dataset into SQLite
Code/scripts/sync_simple_syllabus.py Optional local Simple Syllabus browser import
Code/requirements.txt             Python dependencies
Dataset/                          Dataset file and dataset description
Results/                          Screenshots and result summary
Report PDF/                       Final PDF report
Presentation Slides/              PowerPoint presentation
README/                           This README file
```

## Notes

This submission is designed for local execution. After extracting the zip file, open the `Code` folder, install dependencies, run the seed script, start Uvicorn, and open `http://127.0.0.1:8000`.
