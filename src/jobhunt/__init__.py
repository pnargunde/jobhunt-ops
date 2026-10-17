"""jobhunt: automated job search, match scoring, and tailored resume/cover-letter generation.

This package is deliberately structured so every capability is a plain, importable
Python function (see config.py, db.py, llm.py, profile_builder.py, matcher.py,
docgen.py, report.py, summary.py). The CLI (cli.py) is a thin wrapper around
these functions so the same logic can later be reused by other front-ends
(e.g. a Copilot CLI custom skill) without duplication.
"""

__version__ = "0.1.0"
