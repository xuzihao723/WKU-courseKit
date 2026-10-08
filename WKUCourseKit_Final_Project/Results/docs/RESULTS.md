# Results Summary

This project produces a working Python web application.

## Program Output

After running:

```powershell
python scripts/seed_db.py
uvicorn app.main:app --reload
```

the application displays these main pages:

- `/courses`: enrolled course list with search, term filter, subject filter, material count, and print action
- `/library`: syllabus library search with filters by course code, title, instructor, term, subject, and material availability
- `/courses/{course_id}`: course detail and syllabus reader
- `/materials`: required and optional materials with ISBN and source links
- `/print`: print-friendly syllabus packet and material checklist

## Seed Output

The included dataset creates:

- 2 terms
- 8 instructors
- 8 courses
- 8 syllabi
- 21 syllabus sections
- 7 materials
- 8 course-material links
- 7 student material statuses

## Final Artifacts

- [Final report](../../Report%20PDF/WKUCourseKit_Final_Report.pdf)
- [Presentation](../../Presentation%20Slides/WKUCourseKit_Final_Presentation.pptx)
- `Results/*.png` page screenshots for My Courses, Syllabus Library, Course Detail, Materials, and Print Center

These files document the project problem, methodology, tools, results, conclusion, and presentation flow.
