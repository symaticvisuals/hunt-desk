# Job hunt

This repo is a job search. People, titles, and API keys are data, not code. Read the profile files. Do not assume a profession.

If `profile/og-resume.md` contains the line `PLACEHOLDER_RESUME`, stop and ask for a resume.

## Files

- The person's files are git-ignored: `profile/og-resume.md`, `linkedin-profile.md`, `projects.md`, `preferences.json`, `search.json`, `rubric.json`, `models.json`, `worth.json`, everything under `data/` except `data/defaults/`, and everything under `resumes/`. A fresh clone gets neutral copies from `profile/defaults/` and `data/defaults/` through `uv run python -m hunt seed` (run by `./start`), which never overwrites. Change a shipped default in `profile/defaults/`, never in the person's copy.
- `profile/og-resume.md` is the master resume. Score against it. Copy it when tailoring. Do not edit it unless the user explicitly says to change the master resume.
- `profile/linkedin-profile.md` is extra story. It is not a second resume.
- `profile/projects.md` is in play only when the job description wants someone who builds product, agents, or internal tools.
- `profile/preferences.json` is what the person wants: target roles, seniority, years, cities, work modes, pay floor, company size, job types, and excluded titles. Saving it in the desk writes keywords, excluded titles, and location into `search.json`, and the years cap and `comp.min` into `rubric.json`. Edit preferences, not those fields, when the person's wants change.
- `profile/search.json` is the only title, location, and source filter. A title matches a target role when it has all of the role's key words in any order: full stack and fullstack, developer and engineer and engg, ML and machine learning, GenAI and generative AI, and plurals count as the same word, and "software" or "application" in a role is optional when something more specific remains (`hunt/titles.py`). The CLI already applied it. Location mode `places` means the cities, countries, and work modes from preferences.
- `data/inbox.json` holds every role ever fetched that matched a target role and place. Nothing is removed from it; stale, closed, dismissed, and applied roles are hidden instead. `data/last-search.json` holds only the newest full search, which is what `/job-hunt` scores.
- `profile/rubric.json` is the only scoring math. Do not invent a bonus or a penalty that is not in that file.
- `data/evaluated-jobs.csv` is every role already scored. The CLI dedupes against it. Do not load the whole file into context when the CLI can classify a batch.
- `data/job-tracker.csv` is application status. You never set `applied` to Yes, and you never change a status. The user does that after they apply.
- `data/watchlist.json` is companies and ATS slugs. Suggest additions in the digest. Do not add a company unless the user accepts it.
- `profile/worth.json` is the worth rules: the words that mark a company as staffing, training, or services, what each class does, the phrases that make a US remote role open or closed to the person, and the description and age limits. `data/companies.json` holds a class the person set for a company, and `data/worth-overrides.json` holds roles the person kept anyway. Do not edit either data file for the person.

## Rubric

Read `profile/rubric.json` and apply it.

- Base score is 0–100 from fit to the master resume.
- `natural_hire_at` and above means a natural hire, not a stretch. Say so in the digest.
- A gap that would screen the candidate out (years, a required tool they have never shipped, work authorization they cannot meet) cannot finish above `screenout_cap`.
- Each missing required platform subtracts `missing_platform_penalty`.
- If the posting asks for more years than `max_years_without_cap`, cap the score at `overlevel_cap`.
- A watchlist company is a big organisation unless that row sets `"big": false`. When a big organisation asks for more years than `max_years_without_cap`, the role is removed before the classifier and before Claude. A smaller company keeps the score cap only.
- Apply `comp_penalty` only when `comp.max` is set and the posting's floor is far above it. A null comp band means no comp penalty.
- Apply a bonus only when its `when` text matches. Record the bonus id.
- Log the base score, each penalty id, and the final score.
- A classifier fit below `plausible_at` is a stretch and is never scored. The fit label rounds half up, so `plausible_at` 2.5 means the label and the gate agree.

Location is not a score by itself. `search.json` already decided whether the row is eligible. Record the location and any work-authorization line.

## Desk

`uv run python -m hunt` opens the Textual desk. It opens on the first unfinished step: Step 1 is the resume (rework with Claude or use as is), Step 2 is preferences (Claude proposes from the resume, the person edits and saves), then the job list. Resume, preference, key, and company edits made there are already written to the profile files. Prefer `uv run python -m hunt search` when you only need to fetch.

`./start` installs uv, the packages, and Tectonic, seeds the defaults, and opens the desk. On a fresh clone without a key, the desk asks for the Anthropic API key first. Step 2 also sets the two daily caps (`budget` in `profile/preferences.json`) and `max_posted_days`, and saving it turns on the schedule. A checkout that saved preferences before caps existed sees a one-time caps screen. Every fetch classifies new roles with the `classifier` set in `profile/models.json`. `haiku` (the default for a fresh clone) runs the `classify` step (Claude Haiku) through the Claude API, billed to `ANTHROPIC_API_KEY` and stopped for the day at the classify cap. `clef` runs Clef-flash locally and does not call Claude; when Ollama is down and a key is set, roles go to Haiku instead. Settings in the desk switches between them. Roles already classified keep their decision after a switch. Every role also gets a worth verdict from `profile/worth.json` and `data/companies.json`: `skip` hides it with the reason, `maybe` keeps it on the list with a `?` and the reason, `c` sets a company's class for good, and `k` keeps one role. Years come only from experience sentences: a range counts as its low end, numbers above 15 are ignored, and the desk shows the sentence. The main list shows only roles that matter. A role is hidden with a reason when it was dismissed, applied, is older than 14 days and no fetch listed it in the last 3 days, was posted more than `max_posted_days` ago (`profile/worth.json`, 0 turns it off; an undated role counts from the day hunt first saw it), is not a target role or seniority, is in the wrong place or job type, pays below the floor, is the wrong company size, is closed, asks a big organisation's years above the cap, scored at or below `screenout_cap`, a worth rule skipped it (staffing, training, remote US: US only, no description), or the classifier did not keep it as a plausible match. `h` shows hidden roles with their reasons, and `tab` shows applied roles. In the applied view, `u` undoes an apply the user marked by mistake: the tracker row goes, a scored row keeps its score, and a row that only recorded the apply is removed. The desk checks the data and profile files every 15 seconds and reloads when one changed, so roles a scheduled tick adds appear without a restart, and the highlighted role stays highlighted. Claude runs only on roles that pass, unless the user presses `S` on a role. Scoring runs `claude -p` with read-only tools, up to three roles at once, and the desk stays usable while they run; hunt writes the row to `data/evaluated-jobs.csv`. A score above `screenout_cap` also tailors the resume for that job right away: Claude returns a structured resume (`TAILOR_SCHEMA`), the changes, and the gaps, following `RESUME_RULES` in `hunt/tailor.py` and the humanizer in `prompts/humanizer/`. Hunt renders it into Jake Gutierrez's template (`templates/jake.tex`), compiles it with Tectonic, cuts the least important bullets until it fits one page, runs the ATS checks on the PDF's text, and writes `resumes/<company>/<role>/resume.pdf`, `resume.tex`, `resume.json`, `ats.json`, `jd.md`, and `notes.md`. `notes.md` lists any number that is not in the master resume, the cuts, and the ATS check. A role whose PDF did not build, ran over a page, or failed a check shows ⚠. Folders with only `resume.md` are older output, count as tailored, and are not redone. `t` tailors a scored role now, `v` opens its PDF, and `y` copies a prompt for Claude.ai; `hunt resume check <file.tex>` checks what comes back, and `hunt resume build` builds the PDFs for resumes tailored before a TeX engine was installed (`./start` runs it). `profile/og-resume.md` is never changed by tailoring. Without the claude CLI, `s` and `t` use the Messages API with a key. `A` in the desk scores every unscored role on the main list through the Message Batches API instead: one batch at half the API price, billed to `ANTHROPIC_API_KEY`, then a second batch tailors the roles above `screenout_cap`. Every batch reserves an upper-bound estimate in `data/spend.json` and settles at the billed amount; when the roles do not fit today's score cap, `A` offers to send what fits or all of them. Open batches are in `data/batches.json`; the desk and every scheduled tick apply finished ones, and `hunt batch` does the same from a terminal. Models and effort for both batches are the `score` and `tailor` steps in `profile/models.json`. The description Claude receives already has benefits and legal boilerplate removed. The list is ordered newest posting first. `o` opens the apply link and `x` hides a role for good. `uv run python -m hunt sameness` reports what share of their lines any two tailored resumes under `resumes/` have in common, counting a reworded or reordered line as the same line. A high average means tailoring is reordering the same lines rather than choosing between them.

## Schedule

`uv run python -m hunt schedule install` adds a launchd agent that runs `hunt tick` every 15 minutes. A tick skips quiet hours (23:00 to 07:00 by default), runs only the boards that are due, merges new roles into the inbox, fetches the posting text for Greenhouse and Workday roles that came without it, classifies them with the selected classifier, checks that shortlisted postings are still open, and sends a macOS notification when a role newly reaches the main list. Each board's interval adapts: faster when it finds new roles, slower after empty runs, and backed off when it is blocked. A tick tailors roles above the cutoff that have no resume yet, then scores unscored roles on the main list (clean roles before `?` roles), through batches within the daily caps in `preferences.json`, and never before those caps have been saved once. With both caps at 0, a tick never calls Claude. A tick with no board due still does this step. `hunt schedule status` shows each board's next run. See `docs/schedule.md`.

## Workflows

Resume rewrite can run through the Claude API: `uv run python -m hunt run api resume-rewrite`. Models and effort live in `profile/models.json`. The same rewrite can run as one Managed Agents session after `ant apply agents environments`: `uv run python -m hunt run managed resume-rewrite`. Both write `data/resume-rewrite.md`. The API rewrite also writes the draft to `resumes/updated-resume.md`, and replaces `profile/og-resume.md` only when the fact check passes, the resume has an experience section to replace, and no `[metric]` is left. Each replacement first copies the current master to `profile/og-resume.md.bak`. The managed session leaves `profile/og-resume.md` unchanged. Grok and Claude Code still have a copy of the workflow for an interactive session.

### Paste a job description

Dedupe with `python3 -m hunt dedupe` (one JSON object per line: company, role, url, platform, job_id). Score new rows. Append them to `data/evaluated-jobs.csv` in one write. Stop. Do not tailor.

### Find jobs

Follow `.claude/skills/job-hunt/SKILL.md`. Fetch with `python3 -m hunt search` before any web search. Fewer than five new roles is a complete result. Dead links, duplicates, and excluded titles go in a Skipped section with the reason. Never invent a posting. Aggregator URLs (Arbeitnow, Jobicy, Muse, Himalayas, Adzuna, Jooble) are leads, not verification. Prefer the company's Greenhouse, Lever, Ashby, or Workday JSON.

### Apply

Only when the user says to apply. Copy `profile/og-resume.md` to `resumes/<company>/`. Save the job description as `jd.md`. Tailor by reordering and reframing lines that are already true. Do not add employers, titles, or metrics. Do not set `applied`.

## Voice

Cover letters, tailored resumes, and application answers follow the humanizer skill. Keep every fact that is already in the resume. Do not add a name, number, date, or tool. No em dashes, no "not X but Y" contrasts, no "Trigger / Audience" labels, no three parallel examples in a row. Vary sentence length. Lead with a specific system the candidate shipped.

## Failure

If a command fails or a link is dead, write that in the digest and continue. Do not fill the gap with a plausible job.
