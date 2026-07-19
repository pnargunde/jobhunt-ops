"""Single-page summary of all high-matchScore jobs ready for application, with
links to their generated resume/cover letter PDFs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jobhunt.db import get_all_jobs


def build_summary(conn: sqlite3.Connection, min_score: int, output_dir: Path) -> Path:
    rows = [r for r in get_all_jobs(conn) if (r["match_score"] or 0) >= min_score]

    items_html = []
    for row in rows:
        resume_link = (
            f'<a href="{Path(row["resume_path"]).name}">Resume</a>' if row["resume_path"] else "Not generated"
        )
        cover_link = (
            f'<a href="{Path(row["cover_letter_path"]).name}">Cover Letter</a>'
            if row["cover_letter_path"]
            else "Not generated"
        )
        items_html.append(
            f"""
        <li class="job">
          <div class="job-title"><a href="{row['url']}">{row['title']}</a> — {row['company']}</div>
          <div class="job-meta">{row['portal']} | {row['location']} | {row['salary'] or 'Salary not listed'} |
              matchScore: <strong>{row['match_score']}</strong> | status: {row['status']}</div>
          <div class="job-docs">{resume_link} &nbsp;|&nbsp; {cover_link}</div>
        </li>"""
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Jobs Ready for Application</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; max-width: 900px; }}
h1 {{ font-size: 20px; }}
ul {{ list-style: none; padding: 0; }}
.job {{ border: 1px solid #ddd; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; }}
.job-title {{ font-size: 15px; font-weight: bold; }}
.job-meta {{ font-size: 12.5px; color: #555; margin: 4px 0; }}
.job-docs a {{ margin-right: 6px; }}
</style></head>
<body>
<h1>Jobs Ready for Application (matchScore &ge; {min_score})</h1>
<p>{len(rows)} job(s) meet the threshold.</p>
<ul>{''.join(items_html)}</ul>
</body></html>"""

    out_path = output_dir / "summary.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
