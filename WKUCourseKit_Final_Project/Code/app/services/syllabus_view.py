from __future__ import annotations

import re
from html import unescape
from typing import Any


ASSESSMENT_ROWS = [
    ("1", "Resume", "100", "10"),
    ("2", "Elevator Pitch", "100", "10"),
    ("3", "Job Interview", "250", "25"),
    ("4", "Proposal Presentation", "300", "30"),
    ("5", "Test", "100", "10"),
    ("6", "Class Participation", "100", "10"),
    ("7", "Attendance", "50", "5"),
    ("", "TOTAL", "1000", "100"),
]

GRADE_ROWS = [
    ("A", "94% +"),
    ("A-", "90.00% - 93.99%"),
    ("B+", "87.00% - 89.99%"),
    ("B", "84.00% - 86.99%"),
    ("B-", "80.00% - 83.99%"),
    ("C+", "77.00% - 79.99%"),
    ("C", "70.00% - 76.99%"),
    ("D", "60.00% - 69.99%"),
    ("F", "<59.99%"),
]

SECTION_HEADING_ZH = {
    "course description from catalog": "课程简介",
    "class information": "课程信息",
    "instructor information": "教师信息",
    "materials": "课程材料",
}

BODY_LABEL_ZH = {
    "Course Title": "课程名称",
    "Course Number and Section": "课程编号与班级",
    "Campus Location": "校区",
    "Semester": "学期",
    "Class Meeting Days and Times": "上课日期与时间",
    "Class Meeting Location": "上课地点",
    "Instructor Name": "教师姓名",
    "Office Location": "办公室地点",
    "Office Hours": "办公时间",
    "Email": "邮箱",
}

KNOWN_CATALOG_ZH = {
    "An introduction to principles and importance of oral presentations. Overview of interpersonal and community interaction within business and organizational settings. Application of presenting informative and persuasive reports and research. Formerly COMM 3950.": (
        "本课程介绍口头展示的原则与重要性，概述商务和组织环境中的人际沟通与群体互动，并训练学生进行信息型和说服型报告及研究展示。原课程编号为 COMM 3950。"
    ),
}

CATALOG_PHRASES_ZH = (
    ("An introduction to", "介绍"),
    ("principles and importance of", "的原则与重要性"),
    ("Overview of", "概述"),
    ("Application of", "应用"),
    ("Formerly", "原课程编号为"),
    ("business and organizational settings", "商务和组织环境"),
    ("oral presentations", "口头展示"),
    ("interpersonal and community interaction", "人际沟通与群体互动"),
    ("informative and persuasive reports and research", "信息型和说服型报告及研究展示"),
)

GENERAL_PHRASES_ZH = (
    ("Business & Prof. Comm", "商务与职业沟通"),
    ("WENZHOU-KEAN UNIVERSITY", "温州肯恩大学"),
    ("Wenzhou-Kean University", "温州肯恩大学"),
    ("Monday", "周一"),
    ("Tuesday", "周二"),
    ("Wednesday", "周三"),
    ("Thursday", "周四"),
    ("Friday", "周五"),
    ("Saturday", "周六"),
    ("Sunday", "周日"),
    ("M W", "周一、周三"),
    ("T R", "周二、周四"),
    ("From", "从"),
    ("To", "至"),
)

NOISY_SUMMARY_MARKERS = (
    "Program Learning Outcomes",
    "NACE Career Competencies",
    "Components of the Course Grade",
    "Digital Gradebook Requirement",
)


def course_meeting_time(course: Any) -> str:
    for section in getattr(getattr(course, "syllabus", None), "sections", []) or []:
        if str(getattr(section, "heading", "")).strip().lower() != "class information":
            continue
        body = str(getattr(section, "body", "") or "")
        match = re.search(r"Class Meeting Days and Times:\s*(.+)", body, flags=re.IGNORECASE)
        if match:
            return match.group(1).splitlines()[0].strip()
    return ""


def section_kind(section: Any) -> str:
    heading = str(getattr(section, "heading", "")).strip().lower()
    if "required texts" in heading or "optional texts" in heading or "texts and materials" in heading:
        return "materials"
    if "topics and assignments" in heading:
        return "topics"
    if "grading" in heading:
        return "grading"
    return "text"


def course_catalog_description(course: Any, lang: str = "en") -> str:
    for section in getattr(getattr(course, "syllabus", None), "sections", []) or []:
        heading = str(getattr(section, "heading", "") or "").strip().lower()
        if "course description from catalog" in heading:
            return localized_syllabus_body(str(getattr(section, "body", "") or ""), lang)
    summary = str(getattr(getattr(course, "syllabus", None), "summary", "") or "")
    if summary and not any(marker in summary for marker in NOISY_SUMMARY_MARKERS):
        return localized_syllabus_body(summary, lang)
    return ""


def course_detail_sections(course: Any) -> list[Any]:
    visible: list[Any] = []
    for section in getattr(getattr(course, "syllabus", None), "sections", []) or []:
        heading = str(getattr(section, "heading", "") or "").strip().lower()
        if heading in {"class information", "instructor information"}:
            visible.append(section)
    return visible


def localized_section_heading(section: Any, lang: str = "en") -> str:
    heading = str(getattr(section, "heading", "") or "")
    if lang != "zh":
        return heading
    return SECTION_HEADING_ZH.get(heading.strip().lower(), heading)


def localized_syllabus_body(body: str, lang: str = "en") -> str:
    text = normalize_body_text(body)
    if lang != "zh":
        return text
    exact = KNOWN_CATALOG_ZH.get(text)
    if exact:
        return exact
    text = translate_labeled_lines(text)
    for english, chinese in GENERAL_PHRASES_ZH:
        text = text.replace(english, chinese)
    for english, chinese in CATALOG_PHRASES_ZH:
        text = text.replace(english, chinese)
    return text


def localized_labeled_body_lines(body: str, lang: str = "en") -> list[dict[str, str]]:
    text = localized_syllabus_body(body, lang)
    lines: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append({"label": "", "value": ""})
            continue
        match = re.match(r"^([^:：]{1,80})[:：]\s*(.*)$", line)
        if match:
            lines.append({"label": match.group(1).strip(), "value": match.group(2).strip()})
        else:
            lines.append({"label": "", "value": line})
    return lines


def normalize_body_text(value: str) -> str:
    return unescape(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def translate_labeled_lines(value: str) -> str:
    output: list[str] = []
    for line in value.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z ]+):\s*(.*)$", line.strip())
        if not match:
            output.append(line)
            continue
        label = BODY_LABEL_ZH.get(match.group(1), match.group(1))
        output.append(f"{label}: {match.group(2)}")
    return "\n".join(output)


def topic_table(section: Any) -> dict[str, Any]:
    rows = parse_labeled_rows(str(getattr(section, "body", "") or ""))
    if not rows:
        return {"headers": ["Topic"], "rows": []}
    preferred = ["Week", "Date", "Topic", "Assignments", "Readings", "Due"]
    headers = [header for header in preferred if any(row.get(header) for row in rows)]
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    return {"headers": headers[:6], "rows": rows}


def assessment_rows(section: Any) -> list[tuple[str, str, str, str]]:
    body = str(getattr(section, "body", "") or "")
    if "Resume" in body and "Elevator Pitch" in body and "Proposal Presentation" in body:
        return ASSESSMENT_ROWS
    rows = parse_labeled_rows(body)
    output: list[tuple[str, str, str, str]] = []
    for row in rows:
        assessment = row.get("Assessment") or row.get("Assignment") or row.get("Item") or row.get("Name")
        if not assessment:
            continue
        output.append(
            (
                row.get("No", row.get("#", "")),
                assessment,
                row.get("Point Value", row.get("Points", "")),
                row.get("Weighting (%)", row.get("Weight", "")),
            )
        )
    return output


def grade_rows(section: Any) -> list[tuple[str, str]]:
    body = str(getattr(section, "body", "") or "")
    if "94%" in body and "93.99%" in body and "<59.99%" in body:
        return GRADE_ROWS
    matches = re.findall(r"\b(A-|A|B\+|B-|B|C\+|C|D|F)\s+([<>]?\d+(?:\.\d+)?%\s*(?:[+-]|-\s*\d+(?:\.\d+)?%)?)", body)
    return [(grade, percentage.strip()) for grade, percentage in matches]


def parse_labeled_rows(body: str) -> list[dict[str, str]]:
    text = normalize_spaces(body)
    if not text:
        return []
    labels = re.findall(r"([A-Z][A-Za-z /()%#&-]{0,32}):", text)
    if not labels:
        lines = [line.strip(" ;") for line in body.splitlines() if line.strip()]
        return [{"Topic": line} for line in lines]

    row_starts = [match.start() for match in re.finditer(r"(?<![A-Za-z])(?:Week|Date|No|#|Topic|Assessment|Assignment):", text)]
    chunks: list[str] = []
    if row_starts:
        for index, start in enumerate(row_starts):
            end = row_starts[index + 1] if index + 1 < len(row_starts) else len(text)
            chunks.append(text[start:end].strip())
    else:
        chunks = [line.strip() for line in re.split(r"\n+", body) if line.strip()]

    rows = [parse_labeled_chunk(chunk) for chunk in chunks]
    return [row for row in rows if row]


def parse_labeled_chunk(chunk: str) -> dict[str, str]:
    matches = list(re.finditer(r"([A-Z][A-Za-z /()%#&-]{0,32}):", chunk))
    row: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = normalize_label(match.group(1))
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(chunk)
        value = chunk[value_start:value_end].strip(" ;")
        if key and value:
            row[key] = value
    return row


def normalize_label(value: str) -> str:
    text = normalize_spaces(value).strip(" :")
    aliases = {
        "Point value": "Point Value",
        "Weighting": "Weighting (%)",
        "Weighting %": "Weighting (%)",
        "Weight": "Weighting (%)",
        "No.": "No",
    }
    return aliases.get(text, text)


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
