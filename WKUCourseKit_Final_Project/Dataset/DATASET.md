# Dataset Description

## Dataset Included

This project includes a local mock Simple Syllabus-style dataset:

- `data/mock_syllabus.json`

The dataset is bundled with the submission, so no external download is required.

## What It Contains

The JSON file contains realistic course-planning records for WKU/Kean students:

- Academic terms
- Course subjects, numbers, sections, and titles
- Instructor names, emails, departments, and offices
- Syllabus summaries and ordered syllabus sections
- Required and optional course materials
- ISBN values and material metadata
- Demo student material checklist statuses

## How The Dataset Is Used

Run:

```powershell
python scripts/seed_db.py
```

The seed script loads `data/mock_syllabus.json` into SQLite and creates:

- Terms
- Instructors
- Courses
- Enrollments
- Syllabi
- Syllabus sections
- Materials
- Course-material links
- Student material statuses

The web application then displays this data through the My Courses, Syllabus Library, Course Detail, Materials, and Print Center pages.

## Notes

The dataset is for demonstration. It does not contain student passwords, private Kean credentials, or copyrighted textbook files.
