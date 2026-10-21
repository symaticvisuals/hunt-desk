---
name: job-hunt
description: Search, score, and log jobs from this repo. Use when the user says find jobs, score a job description, run the daily hunt, or check the boards.
---

# Job hunt

Fetch and dedupe with the CLI. Score with the profile files. Do not assume a profession.

1. Read `CLAUDE.md`, `profile/rubric.json`, and `profile/search.json`.
2. If `profile/og-resume.md` contains `PLACEHOLDER_RESUME`, stop and ask for a resume.
3. Run `python3 -m hunt search`. Read `data/last-search.json` for the new jobs and their descriptions. Do not open the boards again for those rows.
4. Optional, and only after the CLI: web-search at most five more roles that match `profile/search.json` keywords. Skip titles the CLI already returned. One fetch per survivor. Prefer the company's ATS JSON. Aggregator pages are not verification.
5. Run `uv run python -m hunt classify --limit 15` before any Claude score. It uses the classifier set in `profile/models.json` (Clef-flash or Haiku). Score with Claude only the roles it marks keep. A skipped role stays skipped unless the user says to score it anyway. The posting text is already shortened. Score each kept role against `profile/og-resume.md` using only `profile/rubric.json`. Read `profile/projects.md` only when the posting wants a builder. Read `profile/linkedin-profile.md` for wording, not for new facts.
6. Append every scored role to `data/evaluated-jobs.csv` in one write. Columns are the CSV header. Leave `applied` empty.
7. Write `daily-digest/YYYY-MM-DD.md` with three parts: roles at or above `natural_hire_at`, other scored roles, and Skipped (duplicate, dead link, excluded title, command error) with the reason. Fewer than five new roles is success. Never invent a posting.

When you rewrite a resume line, cover letter, or application answer, follow the humanizer skill. Keep every fact already in the source. Do not add a name, number, date, or tool.

Do not push. Do not edit `profile/og-resume.md`. Do not mark anything applied.
