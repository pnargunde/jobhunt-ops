"""Report generation: dumps the jobs table as CSV and HTML with the exact
columns requested: url, first_seen, portal, title, company, status, location,
salary, matchScore — sorted by matchScore descending.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from jobhunt.db import get_all_jobs

COLUMNS = ["url", "first_seen", "portal", "title", "company", "status", "location", "salary", "matchScore"]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "url": row["url"],
        "first_seen": row["first_seen"],
        "portal": row["portal"],
        "title": row["title"],
        "company": row["company"],
        "status": row["status"],
        "location": row["location"],
        "salary": row["salary"] or "Not listed",
        "matchScore": row["match_score"] if row["match_score"] is not None else "",
    }


def build_report_rows(conn: sqlite3.Connection) -> list[dict]:
    return [_row_to_dict(r) for r in get_all_jobs(conn)]


def write_csv(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def write_html(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header_html = "".join(f"<th>{c}</th>" for c in COLUMNS)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{row[c]}</td>" for c in COLUMNS)
        body_rows.append(f"<tr>{cells}</tr>")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>jobhunt report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
th {{ background: #f2f2f2; }}
tr:nth-child(even) {{ background: #fafafa; }}
</style></head>
<body>
<h1>Job Search Report</h1>
<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_report(conn: sqlite3.Connection, output_dir: Path, formats: list[str]) -> dict[str, Path]:
    rows = build_report_rows(conn)
    results: dict[str, Path] = {}
    if "csv" in formats:
        results["csv"] = write_csv(rows, output_dir / "report.csv")
    if "html" in formats:
        results["html"] = write_html(rows, output_dir / "report.html")
    return results
